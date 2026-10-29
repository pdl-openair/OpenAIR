# Precheck Report

## Source

Uploaded file: `outputs.zip`

## Loaded cases

Total cases loaded: 12

## Filter rule shape

Observed `downstream_filter_rules` item types:

```json
{
  "dict": 71
}
```

## Important finding

Actual Skill 1 outputs contain object-shaped `downstream_filter_rules`.

If Skill 2 P0 input schema only accepts string items for `downstream_filter_rules`, then the raw replay set will fail before query generation begins.

This should be treated as a **Skill 2 input contract issue**, not as a Skill 1 failure and not as a query-generation quality failure.

## Recommended fix if raw schema validation fails

Allow `downstream_filter_rules.items` to be either a string or an object:

```json
{
  "type": "array",
  "items": {
    "anyOf": [
      { "type": "string" },
      { "type": "object" }
    ]
  }
}
```

Do not silently normalize raw inputs during the official effect validation.
