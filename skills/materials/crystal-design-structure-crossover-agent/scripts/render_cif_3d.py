#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CIF -> 3D ball-and-stick renderer (self-contained, no pymatgen/ase needed).

Parses a crystallographic CIF (lattice params + atom fractional coords),
builds a supercell, and renders a ball-and-stick plot with matplotlib 3D.

Why hand-rolled: the openair env has pymatgen but a broken numpy/scipy ABI
(_pocketfft_umath DLL load fail), and base env has matplotlib+numpy but no
pymatgen. This script only needs numpy + matplotlib, both present in base.

Usage:
    python -I render_cif_3d.py INPUT.cif OUTPUT.png [--supercell N N N] [--title ...]
    python -I render_cif_3d.py INPUT.cif OUTPUT.svg   # vector
"""
import sys
import math
import argparse

# stdlib-only parsing; numpy/matplotlib imported lazily after arg parse
def parse_cif(path):
    """Minimal CIF parser -> (lengths, angles_deg, [(symbol, fract_xyz), ...])."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    lengths = {}
    angles = {}
    atoms = []  # list of (symbol, [u,v,w])
    in_atom_loop = False
    atom_cols = {}  # name -> index
    pending_header = []  # collected _atom_site_* names in order

    lines = text.splitlines()
    i = 0
    # first pass: cell params (single-value _cell_* lines)
    for ln in lines:
        s = ln.strip()
        if s.startswith("_cell_length_a"):   lengths["a"] = float(s.split()[-1])
        elif s.startswith("_cell_length_b"): lengths["b"] = float(s.split()[-1])
        elif s.startswith("_cell_length_c"): lengths["c"] = float(s.split()[-1])
        elif s.startswith("_cell_angle_alpha"): angles["alpha"] = float(s.split()[-1])
        elif s.startswith("_cell_angle_beta"):  angles["beta"]  = float(s.split()[-1])
        elif s.startswith("_cell_angle_gamma"): angles["gamma"] = float(s.split()[-1])

    # second pass: atom_site loop
    in_loop = False
    header = []
    for ln in lines:
        s = ln.strip()
        if s == "loop_":
            in_loop = True
            header = []
            continue
        if in_loop:
            if s.startswith("_atom_site_"):
                header.append(s)
                continue
            if s.startswith("_"):
                # different loop header started without data -> reset
                if header and not all(h.startswith("_atom_site_") for h in header):
                    header = [h for h in [s] if h.startswith("_atom_site_")]
                else:
                    header = [] if not header else header
                continue
            if header and s and not s.startswith("#"):
                # data row for current loop
                if all(h.startswith("_atom_site_") for h in header) and len(header) >= 4:
                    tokens = s.split()
                    if len(tokens) >= len(header):
                        col = {h: idx for idx, h in enumerate(header)}
                        sym_idx = col.get("_atom_site_type_symbol",
                                          col.get("_atom_site_label", None))
                        if sym_idx is not None:
                            sym = tokens[sym_idx]
                            # strip trailing digits from label like "Mg0" -> "Mg"
                            if sym and sym[-1].isdigit():
                                # may be a label like Mg0 -> take leading element-ish
                                base = ""
                                for ch in sym:
                                    if ch.isalpha():
                                        base += ch
                                    else:
                                        break
                                sym = base if base else sym
                            try:
                                u = float(tokens[col["_atom_site_fract_x"]])
                                v = float(tokens[col["_atom_site_fract_y"]])
                                w = float(tokens[col["_atom_site_fract_z"]])
                                atoms.append((sym, [u, v, w]))
                            except (KeyError, ValueError):
                                pass
                            continue
                # if not an atom row, end of this loop
                if header and not s:
                    in_loop = False
                    header = []

    if not (lengths and angles and atoms):
        raise ValueError("CIF parse failed: missing cell params or atom sites")

    L = [lengths["a"], lengths["b"], lengths["c"]]
    A = [angles["alpha"], angles["beta"], angles["gamma"]]
    return L, A, atoms


def lattice_vectors(a, b, c, al, be, ga):
    """Standard crystallographic setting -> 3x3 matrix with rows a_,b_,c_ vectors."""
    al, be, ga = math.radians(al), math.radians(be), math.radians(ga)
    a_ = [a, 0.0, 0.0]
    b_ = [b * math.cos(ga), b * math.sin(ga), 0.0]
    cx = c * math.cos(be)
    cy = c * (math.cos(al) - math.cos(be) * math.cos(ga)) / math.sin(ga)
    cz = math.sqrt(max(c * c - cx * cx - cy * cy, 0.0))
    c_ = [cx, cy, cz]
    return [a_, b_, c_]


# CPK-ish colors + covalent radii (Å) for common elements
CPK = {
    "Mg": "#77DD77", "Ca": "#FFB347", "Si": "#DAA520", "O": "#FF4D4D",
    "Ti": "#B0C4DE", "Zr": "#A0A0A0", "Ba": "#FFD700", "Al": "#BFBFBF",
    "Y":  "#9ACD32", "Hf": "#A0E6FF", "Sr": "#FF8C00", "Li": "#CC77FF",
    "Na": "#AB5CF2", "K":  "#8F40D4", "Rb": "#702EB0", "Cs": "#B573FF",
    "Fe": "#E06633", "Mn": "#95579B", "Cr": "#8A99C7", "Ni": "#39FF14",
    "Co": "#3D59AB", "Cu": "#C88033", "Zn": "#7D80B0",
    "H":  "#FFFFFF", "C": "#404040", "N": "#3050F8", "F": "#90E050",
    "Cl": "#1FF01F", "S": "#FFFF30", "P": "#FF8000", "B": "#FFB5B5",
    "In": "#A6756D", "Ga": "#C28F8F", "Ge": "#668F8F", "Sn": "#668080",
    "Te": "#DDA6BD", "Se": "#FF9933", "Sb": "#D6A6A6", "Bi": "#9E84A8",
}
RADIUS = {  # covalent/ionic-ish radius Å (for bond cutoff + draw size)
    "Mg": 1.30, "Ca": 1.74, "Si": 1.11, "O": 0.73, "Ti": 1.36, "Zr": 1.54,
    "Ba": 1.95, "Al": 1.21, "Y": 1.62, "Hf": 1.52, "Sr": 1.95, "Li": 0.76,
    "Na": 1.02, "K": 1.38, "Rb": 1.52, "Cs": 1.67, "Fe": 1.24, "Mn": 1.21,
    "Cr": 1.24, "Ni": 1.15, "Co": 1.16, "Cu": 1.17, "Zn": 1.22,
    "H": 0.31, "C": 0.76, "N": 0.71, "F": 0.57, "Cl": 1.02, "S": 1.05,
    "P": 1.07, "B": 0.84, "In": 1.42, "Ga": 1.22, "Ge": 1.20, "Sn": 1.39,
    "Te": 1.38, "Se": 1.20, "Sb": 1.39, "Bi": 1.48,
}
DEFAULT_COL = "#AAAAAA"
DEFAULT_RAD = 1.30


def render(cif_path, out_path, supercell, title, bonds=True, bg="white"):
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    from mpl_toolkits.mplot3d.art3d import Line3DCollection

    L, A, atoms = parse_cif(cif_path)
    vecs = lattice_vectors(L[0], L[1], L[2], A[0], A[1], A[2])
    M = np.array(vecs)  # rows are a_, b_, c_

    # build supercell atoms (cartesian)
    sx, sy, sz = supercell
    pts = []  # (x,y,z)
    syms = []
    for (sym, f) in atoms:
        u, v, w = f
        for i in range(sx):
            for j in range(sy):
                for k in range(sz):
                    cart = (u + i) * M[0] + (v + j) * M[1] + (w + k) * M[2]
                    pts.append(cart)
                    syms.append(sym)
    pts = np.array(pts)

    # full supercell box corners
    origin = np.zeros(3)
    corners = []
    for i in (0, sx):
        for j in (0, sy):
            for k in (0, sz):
                corners.append(i * M[0] + j * M[1] + k * M[2])
    corners = np.array(corners)

    # edges of the supercell box
    edges = []
    for a in corners:
        for b in corners:
            if np.allclose(a, b):
                continue
            d = b - a
            d_len = np.linalg.norm(d)
            if d_len < 1e-10:
                continue
            # edge if d is parallel to one lattice vector and length == step * |vec|
            for vec, step in zip(M, supercell):
                vec_len = np.linalg.norm(vec)
                if vec_len < 1e-10:
                    continue
                nrm = vec_len * step
                # check length
                if abs(d_len - nrm) > 1e-4 * nrm:
                    continue
                # check direction (parallel or anti-parallel)
                cos_angle = abs(np.dot(d, vec)) / (d_len * vec_len)
                if abs(cos_angle - 1.0) < 1e-4:
                    edges.append([a, b])
                    break

    fig = plt.figure(figsize=(7, 7), facecolor=bg)
    ax = fig.add_subplot(111, projection="3d", facecolor=bg)

    # bonds: connect atoms within (ra+rb)*0.6 ... actually use 1.2*(ra+rb) tolerant
    if bonds:
        segs = []
        n = len(pts)
        # only draw bonds to avoid O(n^2) blow-up for small supercells it's fine
        if n <= 400:
            for ii in range(n):
                for jj in range(ii + 1, n):
                    d = np.linalg.norm(pts[ii] - pts[jj])
                    ra = RADIUS.get(syms[ii], DEFAULT_RAD)
                    rb = RADIUS.get(syms[jj], DEFAULT_RAD)
                    lo, hi = 0.4, 1.25 * (ra + rb)
                    if lo < d < hi:
                        # only cation-anion or different-element bonds to reduce clutter
                        if syms[ii] != syms[jj]:
                            segs.append([pts[ii], pts[jj]])
            if segs:
                lc = Line3DCollection(segs, colors="#666666", linewidths=1.4, alpha=0.6)
                ax.add_collection3d(lc)

    # unit cell / supercell box
    if edges:
        box = Line3DCollection(edges, colors="#222222", linewidths=1.8, alpha=0.8)
        ax.add_collection3d(box)

    # atoms
    for sym in sorted(set(syms)):
        mask = np.array([s == sym for s in syms])
        if not mask.any():
            continue
        p = pts[mask]
        r = RADIUS.get(sym, DEFAULT_RAD)
        size = (r / 0.7) ** 2 * 90  # area-ish scaling
        col = CPK.get(sym, DEFAULT_COL)
        ax.scatter(p[:, 0], p[:, 1], p[:, 2],
                   s=size, c=col, edgecolors="black", linewidths=0.6,
                   depthshade=True, label=sym, alpha=0.95)

    # labels on a few atoms (first occurrence per element)
    labelled = set()
    for (sym, p) in zip(syms, pts):
        if sym in labelled:
            continue
        labelled.add(sym)
        ax.text(p[0], p[1], p[2], "  " + sym, fontsize=9, color="black")

    ax.set_xlabel("x (Å)"); ax.set_ylabel("y (Å)"); ax.set_zlabel("z (Å)")
    ax.set_title(title or cif_path)
    ax.legend(loc="upper left", bbox_to_anchor=(0.02, 0.98), framealpha=0.9)

    # equal aspect
    try:
        ax.set_box_aspect((1, 1, 1))
    except Exception:
        pass

    plt.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight", facecolor=bg)
    plt.close(fig)
    print(f"OK -> {out_path}  ({len(pts)} atoms, {len(edges)} cell edges)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cif")
    ap.add_argument("out")
    ap.add_argument("--supercell", nargs=3, type=int, default=[2, 2, 2])
    ap.add_argument("--title", default=None)
    ap.add_argument("--no-bonds", action="store_true")
    ap.add_argument("--bg", default="white")
    a = ap.parse_args()
    render(a.cif, a.out, tuple(a.supercell), a.title, bonds=not a.no_bonds, bg=a.bg)


if __name__ == "__main__":
    main()
