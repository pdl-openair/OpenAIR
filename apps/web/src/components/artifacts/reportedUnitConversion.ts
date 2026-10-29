import { parseStrictScalarNumber } from "./researchRecordDataChartProjection";

/** Explicit powers of ten only; no metric interpretation or context inference. */
const UNIT_SCALES: Record<string, { family: string; exponent: number }> = {
  Pa: { family: "pressure", exponent: 0 },
  kPa: { family: "pressure", exponent: 3 },
  MPa: { family: "pressure", exponent: 6 },
  GPa: { family: "pressure", exponent: 9 },
  m: { family: "length", exponent: 0 },
  cm: { family: "length", exponent: -2 },
  mm: { family: "length", exponent: -3 },
  µm: { family: "length", exponent: -6 },
  μm: { family: "length", exponent: -6 },
  um: { family: "length", exponent: -6 },
  nm: { family: "length", exponent: -9 },
  V: { family: "voltage", exponent: 0 },
  mV: { family: "voltage", exponent: -3 },
  A: { family: "current", exponent: 0 },
  mA: { family: "current", exponent: -3 },
  eV: { family: "energy", exponent: 0 },
  meV: { family: "energy", exponent: -3 },
};

export function reportedUnitFamily(unit: string): string {
  return UNIT_SCALES[unit]?.family ?? `exact:${unit}`;
}

/** A deterministic comparison unit for already-supported fixed multipliers. */
export function reportedUnitBase(unit: string): string {
  const family = UNIT_SCALES[unit]?.family;
  return family
    ? (Object.keys(UNIT_SCALES).find(
        (key) =>
          UNIT_SCALES[key].family === family && UNIT_SCALES[key].exponent === 0,
      ) ?? unit)
    : unit;
}

/** Shift the reported decimal text rather than round a floating-point product. */
export function convertReportedUnit(
  reportedValue: string,
  from: string,
  to: string,
): { value: number; label: string } | null {
  const reportedNumber = parseStrictScalarNumber(reportedValue);
  if (reportedNumber === null) return null;
  if (from === to) {
    return { value: reportedNumber, label: reportedValue };
  }
  const source = UNIT_SCALES[from];
  const target = UNIT_SCALES[to];
  if (!source || !target || source.family !== target.family) return null;
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(
    reportedValue.trim(),
  );
  if (!match) return null;
  const [, sign, integer, fraction = "", exponent = "0"] = match;
  const digits = integer + fraction;
  const position =
    integer.length + Number(exponent) + source.exponent - target.exponent;
  // Bounds keep malformed or extreme labels from allocating unbounded strings.
  if (!Number.isSafeInteger(position) || Math.abs(position) > 1000) return null;
  const shifted =
    position <= 0
      ? `0.${"0".repeat(-position)}${digits}`
      : position >= digits.length
        ? digits + "0".repeat(position - digits.length)
        : `${digits.slice(0, position)}.${digits.slice(position)}`;
  const [whole, decimal = ""] = shifted.split(".");
  const normalizedWhole = whole.replace(/^0+(?=\d)/, "");
  const normalizedDecimal = decimal.replace(/0+$/, "");
  const label = `${sign}${normalizedWhole}${normalizedDecimal ? `.${normalizedDecimal}` : ""}`;
  const value = Number(label);
  if (!Number.isFinite(value) || (value === 0 && reportedNumber !== 0))
    return null;
  return { value, label };
}
