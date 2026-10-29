import subprocess

# Get dev version
r1 = subprocess.run(
    ["git", "show", "origin/dev:evals/openair-product-harness/tests/06-artifact-unavailable-recovery.spec.ts"],
    capture_output=True
)
# Get current branch version
r2 = subprocess.run(
    ["git", "show", "HEAD:evals/openair-product-harness/tests/06-artifact-unavailable-recovery.spec.ts"],
    capture_output=True
)

dev_lines = r1.stdout.decode('utf-8').splitlines()
cur_lines = r2.stdout.decode('utf-8').splitlines()

print("=== DEV VERSION (first 15 + key lines) ===")
for i, line in enumerate(dev_lines):
    if i < 15 or i in [46, 47, 48, 49]:
        print(f"L{i+1}: {line}")

print("\n=== CURRENT BRANCH (first 15 + key lines) ===")
for i, line in enumerate(cur_lines):
    if i < 15 or i in [46, 47, 48, 49]:
        print(f"L{i+1}: {line}")
