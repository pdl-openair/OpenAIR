# P5 Contract Validation — BO Candidate Generation

The public `candidates[].parameters` contract uses canonical PVD names `power`, `n2_flow`, `ar_flow`, and `deposition_time` plus explicit units. Internal optimizer feature names remain internal and are projected once at the BO public-output boundary.

A real succeeded BO execution produced 10 candidates under the revised public contract. BO regression: **12/12 PASS**. The complete succeeded output is accepted unchanged by Node7 v1.1.0.

BO remains a candidate generator only; it does not validate feasibility or form the final recommendation.
