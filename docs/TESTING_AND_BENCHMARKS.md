# Testing and Benchmarking Strategy

## 1. Test layers

- Unit: validators, path policy, SSRF policy, providers' parsers, retry, cache, results, approvals, transformations.
- Integration: MCP server startup, tool list/schema, SQLite, provider mock servers, document fixtures, browser test pages, subprocess runner.
- Compatibility: actual LM Studio Host + known local models and MCP Inspector/SDK client.
- Security: adversarial inputs and boundary tests listed in `SECURITY_MODEL.md`.
- Performance: startup, idle RSS, peak RSS, latency, output bytes, throughput/concurrency.
- Model evaluation: task suites exercising tool selection, arguments, multi-step use, error recovery, evidence, and safety.

## 2. Deterministic tests

Use mocked HTTP responses and local fixtures. Tests must not depend on live search, current market values, YouTube quota, or weather endpoints. Add a small opt-in smoke test for live providers; it must not run in normal CI.

## 3. MCP protocol tests

Test:
- server initializes and exposes expected tools for each profile;
- every tool schema is valid and name is unique;
- valid and invalid arguments are handled correctly;
- errors are distinguishable from successful empty results;
- stdio output contains no non-protocol stdout noise;
- cancellation/timeouts work where supported;
- large outputs are bounded and paginated;
- profile selection changes the exposed tool list after restart.

Use the official MCP Inspector during development, but do not treat Inspector success as proof that LM Studio model behavior works.

## 4. Model compatibility dataset

Store tasks in versioned JSONL/YAML fixtures. Each case should include:
- task ID and domain;
- user request;
- expected tool or acceptable tool sequence;
- argument constraints;
- required facts/evidence;
- prohibited side effects;
- expected final outcome;
- scoring rubric;
- deterministic mock/provider fixture where possible.

Test a range of local model sizes and tool-calling templates only when available. Record exact model identifier, quantization, context size, temperature/top-p/repetition settings, LM Studio version, server version, profile, OS, and GPU offload settings.

## 5. Scorecard

Measure separately:
- correct tool selection;
- argument/schema correctness;
- successful execution;
- complete task success;
- answer/fact accuracy;
- evidence/provenance quality;
- recovery after transient and permanent errors;
- permission/safety compliance;
- tokens/tool-description context cost;
- total latency and tool latency;
- server memory/CPU overhead.

Do not collapse all dimensions into one score only. A model can have high tool-call accuracy but poor final answer quality or unsafe behavior.

## 6. Baselines and thresholds

Phase 0 establishes the baseline. Set release thresholds after measuring representative tasks. Security invariants are absolute: no approval bypass and no critical boundary escape. For model-dependent scores, track regression and confidence intervals across repeated runs where nondeterminism matters. Do not invent a benchmark score or claim parity with commercial models.

## 7. Performance methodology

Run the same fixture set with cold and warm cache. Record hardware, OS, Python version, module profile, and enabled providers. Measure default profile first. Optional browser, OCR, embedding, data science, and media modules must be measured separately.

## 8. CI recommendations

Every pull request should run formatting, lint/type checks, unit tests, deterministic integration tests, and security tests. Live network tests and real LM Studio/model benchmarks should be manual or scheduled because they require local services, model weights, credentials, or quota.
