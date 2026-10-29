"""Deterministic finalization for PVD research-request understanding."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

MAX_INITIAL_EXPERIMENTS = 10


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _quote(query: str, value: Any) -> str:
    return value if isinstance(value, str) else ""


def _prov(quote: str) -> dict[str, str]:
    return {"source_id": "q1", "raw_expression": quote}


def _quote_spans(query: str, quote: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while quote:
        position = query.find(quote, start)
        if position < 0:
            return spans
        end = position + len(quote)
        spans.append((position, end))
        start = end
    return spans


def _query_source_record_ref(query: str, *, start: int, end: int) -> str:
    query_digest = hashlib.sha256(query.encode("utf-8")).hexdigest()
    identity = f"q1:{query_digest}:{start}:{end}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return f"pvd-query-source-record:sha256:{digest}"




def _quantity(value: Any, unit: str | None) -> dict[str, Any]:
    q: dict[str, Any] = {"value_raw": str(value)}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        q["value_numeric"] = value
    if unit:
        q["unit_raw"] = unit
    return q


def _unit_fields(row: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    unit = row.get("unit") if isinstance(row.get("unit"), str) and row.get("unit").strip() else None
    identity = row.get("unit_identity") if isinstance(row.get("unit_identity"), str) and row.get("unit_identity").strip() else None
    dimension = row.get("unit_dimension") if isinstance(row.get("unit_dimension"), str) and row.get("unit_dimension").strip() else None
    return unit, identity, dimension


def _assertion(assertion_id: str, statement: str, quote: str) -> dict[str, Any]:
    return {"assertion_id": assertion_id, "identity": "user_fact", "statement": statement, "provenance": _prov(quote)}


def _quantity_assertion(assertion_id: str, value: Any, unit: str | None, quote: str) -> dict[str, Any]:
    return {"assertion_id": assertion_id, "identity": "user_fact", "quantity": _quantity(value, unit), "provenance": _prov(quote)}


def _parameter_spec(row: Mapping[str, Any], quote: str) -> dict[str, Any] | None:
    key = _clean(row.get("key"))
    label = _clean(row.get("label"))
    value_type = _clean(row.get("value_type"))
    allowed = list(row.get("allowed_values") or [])
    unit, identity, dimension = _unit_fields(row)
    spec: dict[str, Any] = {
        "parameter_key": key,
        "display_name": label,
        "value_type": value_type,
        "unit": unit,
        "allowed_values": allowed,
        "rationale": _clean(row.get("rationale")) or f"Explicitly requested parameter: {label}",
        "basis_refs": ["user_query"],
    }
    if identity is not None:
        spec["unit_identity"] = identity
    if dimension is not None:
        spec["unit_dimension"] = dimension
    return spec


def _typed_parameter_value(
    row: Mapping[str, Any],
    *,
    identity: str,
    execution_identity: str | None = None,
    parameter_spec: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    key = _clean(row.get("key"))
    value_type = _clean(row.get("value_type"))
    value = row.get("value")
    quote = row.get("_verified_quote")
    unit, unit_identity, unit_dimension = _unit_fields(row)
    if parameter_spec is not None:
        spec_unit = parameter_spec.get("unit")
        # Only inherit formal unit identity when the raw unit is the same token.
        # Different raw units stay unformalized rather than being guessed equivalent.
        if unit == spec_unit:
            unit_identity = parameter_spec.get("unit_identity")
            unit_dimension = parameter_spec.get("unit_dimension")
    result: dict[str, Any] = {
        "parameter_key": key,
        "value_type": value_type,
        "value": value,
        "unit": unit,
        "unit_identity": unit_identity,
        "unit_dimension": unit_dimension,
        "identity": identity,
        "provenance": _prov(quote),
    }
    if execution_identity is not None:
        result["execution_identity"] = execution_identity
    return result


def _formal_constraint(parameter_key: str, row: Mapping[str, Any], query: str, ordinal: int) -> dict[str, Any] | None:
    quote = _quote(query, row.get("quote"))
    operator = _clean(row.get("operator"))
    values = list(row.get("values") or [])
    valid = (
        (operator in {"min", "max", "exact"} and len(values) == 1)
        or (operator == "range" and len(values) == 2)
        or (operator in {"allowed", "forbidden"} and len(values) >= 1)
    )
    unit = row.get("unit") if isinstance(row.get("unit"), str) and row.get("unit").strip() else None
    out: dict[str, Any] = {
        "constraint_id": f"constraint:{parameter_key}:{ordinal}",
        "identity": "user_fact",
        "kind": "other",
        "field": parameter_key,
        "operator": operator,
        "raw_expression": quote,
        "source_id": "q1",
    }
    if operator == "min": out["lower"] = _quantity(values[0], unit)
    elif operator == "max": out["upper"] = _quantity(values[0], unit)
    elif operator == "range":
        out["lower"], out["upper"] = _quantity(values[0], unit), _quantity(values[1], unit)
    elif operator == "exact": out["exact"] = _quantity(values[0], unit)
    elif operator == "allowed": out["allowed_values"] = values
    elif operator == "forbidden": out["forbidden_values"] = values
    return out


def _distance_mm(value: Any, unit_identity: str | None) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    factors = {"length:mm": 1.0, "length:cm": 10.0, "length:m": 1000.0}
    factor = factors.get(unit_identity or "")
    return None if factor is None else float(value) * factor


def _build_system_context(query: str, system_facts: list[Mapping[str, Any]], materials: list[Mapping[str, Any]]) -> dict[str, Any]:
    facts: dict[str, list[tuple[Any, str, str | None]]] = {}
    for row in system_facts:
        quote = _quote(query, row.get("quote"))
        field = _clean(row.get("field"))
        if not quote or not field or row.get("value") is None:
            continue
        _, unit_identity, _ = _unit_fields(row)
        facts.setdefault(field, []).append((row.get("value"), quote, unit_identity))
    for row in materials:
        role = _clean(row.get("role"))
        quote = _quote(query, row.get("quote"))
        value = _clean(row.get("value"))
        field = "target_material" if role == "target" else "substrate" if role == "substrate" else ""
        if field and quote and value:
            facts.setdefault(field, []).append((value, quote, None))

    core = {
        "target_material": None,
        "substrate": None,
        "target_substrate_distance_mm": None,
        "power_supply_temperature": None,
        "target_angle": None,
    }
    metadata: dict[str, Any] = {}
    field_source = {
        "target_material": "target_material",
        "substrate": "substrate",
        "power_supply_temperature": "power_supply_temperature",
        "target_angle": "target_angle",
    }
    for formal_field, source_field in field_source.items():
        rows = facts.get(source_field, [])
        unique = []
        for value, quote, identity in rows:
            if value not in [v for v, _, _ in unique]: unique.append((value, quote, identity))
        if not unique:
            metadata[formal_field] = {"status":"missing","reason":"not_supplied","provenance":[]}
        elif len(unique) == 1:
            core[formal_field] = unique[0][0]
            metadata[formal_field] = {"status":"known","provenance":[_prov(unique[0][1])]}
        else:
            metadata[formal_field] = {"status":"conflict","reason":"conflicting_explicit_facts","provenance":[_prov(q) for _,q,_ in unique]}

    distance_rows = facts.get("target_substrate_distance", [])
    normalized: list[tuple[float, str]] = []
    for value, quote, identity in distance_rows:
        mm = _distance_mm(value, identity)
        if mm is not None: normalized.append((mm, quote))
    uniq_d: list[tuple[float,str]] = []
    for value, quote in normalized:
        if value not in [v for v,_ in uniq_d]: uniq_d.append((value,quote))
    if not distance_rows:
        metadata["target_substrate_distance_mm"]={"status":"missing","reason":"not_supplied","provenance":[]}
    elif len(uniq_d)==1:
        core["target_substrate_distance_mm"]=uniq_d[0][0]
        metadata["target_substrate_distance_mm"]={"status":"known","provenance":[_prov(uniq_d[0][1])]}
    elif len(uniq_d)>1:
        metadata["target_substrate_distance_mm"]={"status":"conflict","reason":"conflicting_explicit_facts","provenance":[_prov(q) for _,q in uniq_d]}
    else:
        metadata["target_substrate_distance_mm"]={"status":"missing","reason":"explicit_fact_unit_not_formalizable","provenance":[]}

    additional=[]
    core_source_fields={"target_material","substrate","target_substrate_distance","power_supply_temperature","target_angle"}
    for field, rows in facts.items():
        if field in core_source_fields: continue
        for value, quote, _ in rows:
            additional.append({"field": field[:120], "value": value, "metadata":{"status":"known","provenance":[_prov(quote)]}})
    statuses=[m["status"] for m in metadata.values()]
    context_status="conflicted" if "conflict" in statuses else "complete" if all(s=="known" for s in statuses) else "partial"
    return {"contract":"pvd-experimental-system-context/v1","context_status":context_status,**core,"field_metadata":metadata,"additional_facts":additional}


def _empty_equipment(parameter_keys: list[str]) -> dict[str, Any]:
    return {
        "deposition_method":[],"sputtering_mode":[],"target_substrate_distance":[],"chamber":[],"target_size":[],"substrate_temperature":[],
        "gas_and_pressure":{"n2_ar_ratio":[],"total_flow":[],"chamber_pressure":[],"partial_pressures":[]},
        "other_conditions":[],"core_parameters":list(parameter_keys),"parameter_operability":{key:{"status":"unknown"} for key in parameter_keys},
    }


def _build_equipment(query: str, system_facts: list[Mapping[str, Any]], parameters: list[Mapping[str, Any]]) -> dict[str, Any]:
    active_parameter_keys=[_clean(row.get("key")) for row in parameters if _clean(row.get("scope"))=="active" and _clean(row.get("key"))]
    eq=_empty_equipment(active_parameter_keys); counts:dict[str,int]={}
    def aid(prefix:str)->str:
        counts[prefix]=counts.get(prefix,0)+1; return f"equipment:{prefix}:{counts[prefix]}"
    assertion_fields={"deposition_method":"deposition_method","sputtering_mode":"sputtering_mode","chamber":"chamber"}
    quantity_fields={"target_substrate_distance":"target_substrate_distance","target_size":"target_size","substrate_temperature":"substrate_temperature","total_flow":"total_flow","chamber_pressure":"chamber_pressure"}
    for row in system_facts:
        quote=_quote(query,row.get("quote")); field=_clean(row.get("field")); value=row.get("value")
        if not quote or value is None: continue
        unit=row.get("unit") if isinstance(row.get("unit"),str) and row.get("unit").strip() else None
        if field in assertion_fields:
            eq[assertion_fields[field]].append(_assertion(aid(field),str(value),quote))
        elif field in quantity_fields and isinstance(value,(int,float)) and not isinstance(value,bool):
            qa=_quantity_assertion(aid(field),value,unit,quote)
            if field in {"total_flow","chamber_pressure"}: eq["gas_and_pressure"][field].append(qa)
            else: eq[quantity_fields[field]].append(qa)
        elif field == "n2_ar_ratio":
            eq["gas_and_pressure"]["n2_ar_ratio"].append(_assertion(aid(field),str(value),quote))
        else:
            eq["other_conditions"].append(_assertion(aid("other"),f"{field}: {value}",quote))
    # Compatibility view over the current task-defined active scope; never a second scope Authority.
    return eq


def _build_materials(query: str, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    out={"target":{"descriptions":[],"composition":[]},"film":{"descriptions":[],"measured_composition_result_refs":[]},"substrate":[],"layers":[]}
    layer_roles={"electrode","buffer","seed","adhesion","capping","other"}; layer_map:dict[str,list[dict[str,Any]]]={}
    for i,row in enumerate(rows,1):
        quote=_quote(query,row.get("quote")); value=_clean(row.get("value")); role=_clean(row.get("role"))
        if not quote or not value: continue
        a=_assertion(f"material:{role}:{i}",value,quote)
        if role in {"target","target_composition"}: out["target"]["descriptions"].append(a)
        elif role in {"film","measured_film_composition"}: out["film"]["descriptions"].append(a)
        elif role=="substrate": out["substrate"].append(a)
        elif role in layer_roles: layer_map.setdefault(role,[]).append(a)
    out["layers"]=[{"role":role,"assertions":assertions} for role,assertions in layer_map.items()]
    return out


def _build_research_context(query: str, facts: list[Mapping[str, Any]], metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    out={"objectives":[],"process_methods":[],"target_metrics":[],"current_problems":[],"baseline":[],"existing_result_summaries":[],"judgment_criteria":[]}
    mapping={"objective":"objectives","current_problem":"current_problems","baseline":"baseline","existing_result":"existing_result_summaries","judgment_criterion":"judgment_criteria"}
    process_count=0
    for i,row in enumerate(facts,1):
        quote=_quote(query,row.get("quote")); text=_clean(row.get("text")); kind=_clean(row.get("kind"))
        if not quote or not text: continue
        if kind=="process_method":
            process_count += 1
            out["process_methods"].append(_assertion(f"process_method:{process_count}",text,quote))
        elif kind in mapping:
            out[mapping[kind]].append(_assertion(f"research:{kind}:{i}",text,quote))
    for i,row in enumerate(metrics,1):
        quote=_quote(query,row.get("quote")); name=_clean(row.get("name")); direction=_clean(row.get("direction"))
        if quote and name:
            statement=name if direction=="unknown" else f"{name} ({direction})"
            out["target_metrics"].append(_assertion(f"metric:{i}",statement,quote))
    return out


def _measurement(row: Mapping[str, Any], query: str, round_i: int, m_i: int) -> dict[str, Any] | None:
    quote=_quote(query,row.get("quote")); metric=_clean(row.get("metric"))
    value=row.get("value")
    out={"result_id":f"measurement:{round_i}:{m_i}","identity":"measured_result","metric":metric,"provenance":_prov(quote)}
    unit=row.get("unit") if isinstance(row.get("unit"),str) and row.get("unit").strip() else None
    if isinstance(value,(int,float)) and not isinstance(value,bool): out["quantity"]=_quantity(value,unit)
    else: out["qualitative_result"]=value
    return out


def _build_rounds(
    query: str,
    experiments: list[Mapping[str, Any]],
    warnings: list[str],
    parameter_specs: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rounds=[]
    quote_occurrences: dict[str, int] = {}
    for source_i,row in enumerate(experiments,1):
        top_quote=_quote(query,row.get("quote")); identity=_clean(row.get("execution_identity"))
        occurrence_index = quote_occurrences.get(top_quote, 0)
        spans = _quote_spans(query, top_quote)
        quote_occurrences[top_quote] = occurrence_index + 1
        source_start, source_end = spans[occurrence_index] if occurrence_index < len(spans) else (source_i, source_i)
        source_record_ref = _query_source_record_ref(
            query,
            start=source_start,
            end=source_end,
        )
        params=[]
        for p_i,p in enumerate(row.get("parameters") or [],1):
            q=_quote(query,p.get("quote")) if isinstance(p,Mapping) else None
            key=_clean(p.get("key")) if isinstance(p,Mapping) else ""
            spec=parameter_specs.get(key)
            if not isinstance(p,Mapping) or q is None:
                warnings.append(f"existing_parameter_dropped:{source_i}:{p_i}"); continue
            payload=dict(p); payload["_verified_quote"]=q
            formal=_typed_parameter_value(
                payload,
                identity="actual_execution" if identity=="completed_physical_execution" else "user_fact",
                execution_identity="actual" if identity=="completed_physical_execution" else "planned",
                parameter_spec=spec,
            )
            if formal is None: warnings.append(f"existing_parameter_dropped:{source_i}:{p_i}")
            else: params.append(formal)
        measurements=[]
        if identity=="completed_physical_execution":
            for m_i,m in enumerate(row.get("measurements") or [],1):
                if isinstance(m,Mapping):
                    formal=_measurement(m,query,source_i,m_i)
                    if formal is not None: measurements.append(formal)
                    else: warnings.append(f"measurement_dropped:{source_i}:{m_i}")
        elif row.get("measurements"):
            warnings.append(f"planned_measurements_ignored:{source_i}")
        status="measured" if measurements else "executed" if identity=="completed_physical_execution" else "planned"
        rounds.append({
            "round_id":f"local-{len(rounds)+1}","round_ref":None,
            "source_record_ref":source_record_ref,
            "status":status,"recommendation":[],"planned_execution":params if identity=="planned_or_future" else [],
            "actual_execution":params if identity=="completed_physical_execution" else [],"unresolved_execution":[],
            "fixed_conditions":[],"other_process_conditions":[],"measurements":measurements,"notes":[]
        })
    return rounds


def finalize(input_payload: Mapping[str, Any], draft_payload: Mapping[str, Any]) -> FinalizationResult:
    query=input_payload.get("query")
    parameters=[p for p in (draft_payload.get("parameter_mentions") or []) if isinstance(p,Mapping)]
    warnings: list[str]=[]; active=[]; admissions=[]; observations=[]; constraints=[]; unknowns=[]; seen=set()
    for i,row in enumerate(parameters,1):
        quote=_quote(query,row.get("quote")); spec=_parameter_spec(row,quote or "")
        key=_clean(row.get("key"))
        seen.add(key)
        if _clean(row.get("scope"))=="conditional":
            condition=_clean(row.get("condition"))
            admissions.append({
                "judgment_id":f"pvd:parameter_admission:{key}","status":"pending_evidence","user_intent":"evaluate_for_admission",
                **{k:spec[k] for k in ("parameter_key","display_name","value_type","unit","allowed_values")},
                **({"unit_identity":spec["unit_identity"]} if "unit_identity" in spec else {}),
                **({"unit_dimension":spec["unit_dimension"]} if "unit_dimension" in spec else {}),
                "condition":condition,"statement":f"Evaluate evidence before admitting {spec['display_name']} into the formal recommendation parameter set.","provenance":_prov(quote),
            })
        elif _clean(row.get("scope"))=="active": active.append(spec)
        else: active.append(spec)
        for o_i,o in enumerate(row.get("observations") or [],1):
            if not isinstance(o,Mapping): continue
            oq=_quote(query,o.get("quote")); payload={"key":key,"value_type":row.get("value_type"),**dict(o)}
            if oq: payload["_verified_quote"]=oq
            formal=_typed_parameter_value(payload,identity="user_fact",parameter_spec=spec)
            if formal is None: warnings.append(f"parameter_observation_dropped:{key}:{o_i}")
            else: observations.append(formal)
        bad_condition=False
        for c_i,c in enumerate(row.get("constraints") or [],1):
            formal=_formal_constraint(key,c,query,c_i) if isinstance(c,Mapping) else None
            if formal is None: warnings.append(f"parameter_condition_dropped:{key}:{c_i}"); bad_condition=True
            else: constraints.append(formal)
        if bad_condition:
            unknowns.append({"unknown_id":f"unknown:parameter_condition:{key}","identity":"unknown","field":f"parameter_condition:{key}","reason":"A parameter condition was supplied but could not be reliably confirmed.","blocking":False,"source_ids":["q1"]})

    for i,row in enumerate(draft_payload.get("unknowns") or [],1):
        if isinstance(row,Mapping) and _clean(row.get("field")) and _clean(row.get("reason")):
            unknowns.append({"unknown_id":f"unknown:{i}","identity":"unknown","field":_clean(row.get("field")),"reason":_clean(row.get("reason")),"blocking":False,"source_ids":["q1"]})

    material_rows=[r for r in (draft_payload.get("materials") or []) if isinstance(r,Mapping)]
    system_rows=[r for r in (draft_payload.get("system_facts") or []) if isinstance(r,Mapping)]
    research_rows=[r for r in (draft_payload.get("research_facts") or []) if isinstance(r,Mapping)]
    metric_rows=[r for r in (draft_payload.get("target_metrics") or []) if isinstance(r,Mapping)]
    active_specs={row["parameter_key"]:row for row in active}
    rounds=_build_rounds(
        query,
        [r for r in (draft_payload.get("existing_experiments") or []) if isinstance(r,Mapping)],
        warnings,
        active_specs,
    )
    structured={
        "contract":{"kind":"pvd_research_request_understanding","version":"1.0.0"},
        "sources":[{"source_id":"q1","source_type":"query","raw_text":query}],
        "research_context":_build_research_context(query,research_rows,metric_rows),
        "materials":_build_materials(query,material_rows),
        "equipment":_build_equipment(query,system_rows,parameters),
        "experimental_system_context":_build_system_context(query,system_rows,material_rows),
        "rounds":rounds,"hard_constraints":constraints,"derived_values":[],"unknowns":unknowns,"assumptions":[],
        "quality":{"non_blocking":True,"warnings":warnings,"identity_conflicts":[]},
        "parameter_admission_judgments":admissions,"parameter_observations":observations,
    }
    requested=draft_payload.get("initial_experiment_set_count")
    if isinstance(requested,int) and not isinstance(requested,bool) and requested>0:
        initial=min(requested,MAX_INITIAL_EXPERIMENTS); prefs={"initial_experiment_set_count":initial,"source":"user_query","requested_count":requested,"capped_to_maximum":requested>MAX_INITIAL_EXPERIMENTS}
    else:
        prefs={"initial_experiment_set_count":None,"source":"default","requested_count":None,"capped_to_maximum":False}
    return FinalizationResult("succeeded",{"structured_context":structured,"recommendation_parameter_set":{"parameters":active},"run_preferences":prefs})
