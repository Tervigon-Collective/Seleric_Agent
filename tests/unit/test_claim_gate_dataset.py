from seleric_swarm.eval.evaluators import claim_gate_case, load_jsonl
from seleric_swarm.services.claim_gate import validate_claim


def test_numeric_claim_requires_support():
    ok, problems = validate_claim({"claim_type": "numeric", "support_refs": []})
    assert not ok
    assert problems


def test_qualitative_claim_can_pass_without_support_in_base_policy():
    ok, problems = validate_claim({"claim_type": "qualitative", "support_refs": []})
    assert ok
    assert not problems


def test_claim_gate_gold_dataset():
    for case in load_jsonl("eval/datasets/claim_gate.jsonl"):
        assert claim_gate_case(case), case["id"]
