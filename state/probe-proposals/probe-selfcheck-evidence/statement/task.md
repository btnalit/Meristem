# Selfcheck Evidence Probe

This probe runs each organ's selfcheck and verifies that the output includes
data that could only come from the organ's primary function.

For each multi-part organ, the probe:
1. Reads the organ's manifest (organ.json)
2. Invokes the organ's selfcheck op ({"op": "selfcheck"})
3. Checks the output for organ-specific evidence keys with truthy values

Evidence requirements are EXPLICIT per organ — there is no generic fallback:
- failure-aggregator: must produce classification results (a 'class',
  'classification', or 'label' key in at least one result)
- memory-graph: must produce edge/node data (an 'edges' or 'nodes' key
  in at least one result)

Organs NOT in the evidence mapping FAIL the probe. This is deliberate: a
generic 'any extra key beyond name and ok' fallback is the exact weakness
that let FA-019 through (cycle 257 rejections).

Single-file organs are exempt from selfcheck per the germline protocol
and are noted in the detail, not silently skipped. Organs with missing or
unparseable manifests, or missing entrypoints, also FAIL — they are not
silently skipped.

This catches the failure-aggregator case (FA-019) where the selfcheck was
weakened to not test classification: a selfcheck that just runs aggregate
on /dev/null and returns ok:true without any classification results would
fail this probe.
