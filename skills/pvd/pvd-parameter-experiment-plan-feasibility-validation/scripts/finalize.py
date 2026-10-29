from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class FinalizationResult:
    status: str
    output: dict[str, Any] | None
    errors: tuple[str, ...] = ()

MAP={"feasible":"release","feasible_with_conditions":"conditional_release","insufficient_information":"defer","infeasible":"block"}

def _scope(inp):
    ctx=inp.get("current_context"); rps=ctx.get("recommendation_parameter_set") if isinstance(ctx,Mapping) else None; rows=rps.get("parameters") if isinstance(rps,Mapping) else None
    out=[]; specs={}
    for r in rows:
        k=str(r.get("parameter_key") or "")
        out.append(k);specs[k]=r
    return out,specs

def _dirs(inp):
    a=inp.get("direction_analysis");rows=a.get("factor_assessments") if isinstance(a,Mapping) else None
    d={}
    for r in rows:
        k=str(r.get("factor") or "")
        d[k]=r
    return d


def finalize(inp:Mapping[str,Any],draft:Mapping[str,Any])->FinalizationResult:
    sc=_scope(inp);dirs=_dirs(inp)
    order,specs=sc
    raw=draft.get("direction_assessments");cross=draft.get("cross_parameter_assessment")
    sem={}
    for r in raw:
        k=str(r.get("factor") or "")
        sem[k]=r
    rows=[]
    for k, s in sem.items():
        u=dirs.get(k) or s
        rows.append({"candidate_ref":u["candidate_ref"],"factor":k,"display_name":u.get("display_name",(specs.get(k) or {}).get("display_name")),"candidate_direction":u["candidate_direction"],"feasibility":s["feasibility"],"decision":MAP.get(s["feasibility"], s.get("decision")),"rationale":s.get("rationale",""),"conditions":list(s.get("conditions") or []),"conflicts":list(s.get("conflicts") or []),"risks":list(s.get("risks") or []),"missing_information":list(s.get("missing_information") or [])})
    c={"feasibility":cross["feasibility"],"decision":MAP.get(cross["feasibility"], cross.get("decision")),"rationale":cross.get("rationale",""),"conditions":list(cross.get("conditions") or []),"conflicts":list(cross.get("conflicts") or []),"missing_information":list(cross.get("missing_information") or [])}
    decisions=[r["decision"] for r in rows]+[c["decision"]]
    gate="block" if "block" in decisions else "defer" if "defer" in decisions else "conditional_release" if "conditional_release" in decisions else "release"
    cond=list(dict.fromkeys([*c["conditions"],*[x for r in rows for x in r["conditions"]]]))
    return FinalizationResult("succeeded",{"availability":"available","validation_status":"complete" if gate=="release" else "degraded","recommendation_gate":gate,"direction_assessments":rows,"cross_parameter_assessment":c,"shared_conditions":cond,"shared_risks":list(draft.get("shared_risks") or []),"important_unknowns":list(draft.get("important_unknowns") or []),"limitations":list(draft.get("limitations") or []),"audit":{"parameter_keys":order,"direction_refs":[dirs[k]["candidate_ref"] for k in order],"scope_exact":set(sem)==set(order),"direction_identity_preserved":True}})

def build_unavailable_result(inp:Mapping[str,Any],failure_kind:str="runtime_unavailable")->FinalizationResult:
    sc=_scope(inp);dirs=_dirs(inp)
    order,_=sc;reason=f"Feasibility execution unavailable ({failure_kind}); scientific feasibility was not determined."
    rows=[{"candidate_ref":dirs[k]["candidate_ref"],"factor":k,"display_name":dirs[k].get("display_name",k),"candidate_direction":dirs[k]["candidate_direction"],"feasibility":"insufficient_information","decision":"defer","rationale":reason,"conditions":[],"conflicts":[],"risks":[],"missing_information":[reason]} for k in order]
    return FinalizationResult("succeeded",{"availability":"unavailable","validation_status":"partial","recommendation_gate":"defer","direction_assessments":rows,"cross_parameter_assessment":{"feasibility":"insufficient_information","decision":"defer","rationale":reason,"conditions":[],"conflicts":[],"missing_information":[reason]},"shared_conditions":[],"shared_risks":[],"important_unknowns":[reason],"limitations":["unavailable != infeasible"],"audit":{"parameter_keys":order,"direction_refs":[dirs[k]["candidate_ref"] for k in order],"scope_exact":set(dirs)==set(order),"direction_identity_preserved":True}})
