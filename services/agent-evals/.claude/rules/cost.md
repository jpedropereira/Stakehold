# Cost (agent-evals)

Every real-model eval is paid for out of pocket. Keeping cost low wins over coverage when the two
conflict.

## No unattended paid runs

- Never start a real-model eval run on your own initiative, and never add a schedule, cron or
  nightly job for one. A run starts only when the user starts it, and only when a change could move
  Pedro's behaviour.
- Say which suite you'd run, on which models, and the estimated cost, and let the user decide.
- The free smoke run with the scripted fake model (harness wiring only) is fine to run.
- Don't put a paid run in pre-commit, pre-push or CI by default.

## The ceiling is respected

- The runner has a hard cost ceiling per invocation, estimated from the catalogue's pricing before
  starting and checked against actual usage as cells finish. The default is low, sized for a
  `smoke` run on one model.
- Never raise the default ceiling, bypass the guard, or pass the flag that overrides it unless the
  user asked for that specific run.
- Don't work around the guard by splitting one run into several invocations.

## Keep runs small

- Do as much as possible with free checks: deterministic T0/T1 tests and code graders. Use the LLM
  judge only for what code can't judge, since each judge call is another paid call.
- Few cases and few repeats by default. One cloud model by default. Local models cost nothing per
  call and can be added freely.
