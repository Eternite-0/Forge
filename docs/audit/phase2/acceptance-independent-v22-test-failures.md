# V22 complete-suite failures

**Original result: 3 failed, 1,592 passed, 1 warning / 352.37 seconds; 1,595 total cases. Phase 2 remains incomplete.**

All three failures belong to the unchanged legacy native-delivery test and preserve its original assertion requiring `feedback["detail"]["path"] == "confidence"`. V22 omitted that safe field in its schema feedback, raising `KeyError: path`.

- `tests.test_native_delivery_contract_phase2::test_native_worker_repairs_out_of_range_confidence_without_delivery_review[-0.1]`
- `tests.test_native_delivery_contract_phase2::test_native_worker_repairs_out_of_range_confidence_without_delivery_review[1.1]`
- `tests.test_native_delivery_contract_phase2::test_native_worker_repairs_out_of_range_confidence_without_delivery_review[True]`

The [original historical boundary](acceptance-independent-v22-boundary.json) already records `tests.passed=false` and `evidence_consistency_passed=false`. Its Markdown summary shows the passed/warning counts without the failure count; this separate addendum makes the three failures explicit without changing the original bytes. The original JUnit/log and test source hashes are bound in the adjacent JSON. Passing source, archive, static, preservation or accounting checks does not make the failed suite pass.
