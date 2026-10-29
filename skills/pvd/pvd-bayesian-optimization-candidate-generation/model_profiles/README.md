# Model profiles

A model profile binds the reusable BO engine to one scientifically defined optimization domain.

A profile owns the following configuration, not the Python algorithm:

- fixed process-context applicability fields;
- feature names/order and discrete search grid;
- objective metric, transform, and direction;
- declarative candidate constraints;
- GPR/UCB parameters;
- diversity rules and optional group quota;
- immutable baseline dataset and SHA256;
- random seed and batch size.

## Adding a profile

A new profile is appropriate only when there is a scientifically justified training dataset and an explicit applicability domain. Do not create a profile merely to bypass a domain mismatch.

The profile and baseline asset must be versioned and fingerprinted. The baseline remains immutable at runtime; later project observations come from OpenAIR state on every invocation.

Current deterministic constraint kinds:

- `sum_bounds`
- `fraction_bounds`

Current objective transforms:

- `identity`
- `absolute_value`

Current directions:

- `maximize`
- `minimize`

A new profile must pass the Skill regression suite and must not require a case-specific branch in `src/pvd_bo/`.
