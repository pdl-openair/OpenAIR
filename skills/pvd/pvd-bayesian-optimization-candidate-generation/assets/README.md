# Immutable model assets

Files in this directory are versioned baseline model assets, not mutable experiment databases.

`al40sc_mo_110mm_initial_63.json` is a lossless runtime conversion of the supplied 63-row Excel snapshot for the fields consumed by this BO model. It records the source workbook SHA256 and original source row identifiers.

The corresponding model profile stores the JSON asset SHA256. Runtime verifies that fingerprint before fitting.

OpenAIR project observations are never appended to this file. They remain authoritative in OpenAIR research state and are supplied to the stateless Skill at call time.
