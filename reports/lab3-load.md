# ITCS355 Lab 3 — Load Testing and Rollback

## 1. Deployment and Smoke Test

Serving was deployed to Azure Container Apps using the immutable serving image.

- Endpoint: `https://itcs355-predict.blackmoss-9dd1e40c.southeastasia.azurecontainerapps.io`
- Model version: `2`
- `/health`: `200`
- `/ready`: `200`
- `/predict`: `200`
- `/predict/batch`: `200`

The smoke test returned a valid prediction with `model_version: "2"`.

## 2. Load-Test Target

Latency target was chosen before measurement:

> **p95 < 200 ms**

Tests used the `/predict` endpoint with the valid payload defined in `loadtest/k6.js`.

## 3. Load-Test Results

| Concurrency | Throughput (req/s) | p50 (ms) | p95 (ms) | p99 (ms) | Error Rate |
|---:|---:|---:|---:|---:|---:|
| 1 VU | 17.98 | 55.96 | 63.94 | 70.97 | 0% |
| 10 VUs | 42.75 | 103.93 | 754.92 | 847.73 | 0% |
| 50 VUs | 40.43 | 1076.10 | 2078.57 | 3028.64 | 0% |

The first measured concurrency where p95 exceeded the 200 ms target was **10 VUs**, so the observed breaking concurrency was **10 VUs**.

## 4. Cold Start

The Container App used `minReplicas=0`.

A request from zero replicas eventually succeeded after approximately **32.12 s**. This cold-start latency was reported separately from the warmed load-test results.

## 5. Payload-Size Experiment

The request schema was kept unchanged. Payload size was increased using JSON whitespace while preserving valid input.

| Payload Size | Processing Latency (ms) |
|---:|---:|
| 126 B | 0.1466 |
| 1,226 B | 0.1403 |
| 11,126 B | 0.1724 |
| 55,126 B | 0.2157 |
| 110,126 B | 0.2738 |
| 550,126 B | 0.5468 |
| 1,100,126 B | 0.9032 |

Latency remained nearly unchanged for small payloads and began increasing noticeably for very large request bodies.

## 6. Instance-Size Experiment

Baseline instance size:

- CPU: `0.25`
- Memory: `0.5 Gi`

At 10 VUs:

- Throughput: **42.75 req/s**
- p50: **103.93 ms**
- p95: **754.92 ms**
- p99: **847.73 ms**
- Error rate: **0%**

Larger instance:

- CPU: `0.5`
- Memory: `1.0 Gi`

At 10 VUs:

- Throughput: **77.78 req/s**
- p50: **96.64 ms**
- p95: **379.83 ms**
- p99: **481.96 ms**
- Error rate: **0%**

Increasing the instance size increased throughput by approximately **81.9%** and reduced p95 latency by approximately **49.7%**, but p95 remained above the 200 ms target at 10 VUs.

## 7. Batch-Size Experiment

Using a warmed v2 service, one batch request containing 100 rows was compared with 100 separate `/predict` requests.

- 100-row batch: **207 ms**
- 100 separate requests: **8951 ms**
- Batch was approximately **43.2× faster**
- Total latency reduction: approximately **97.7%**
- Error rate: **0%**

Batching was substantially faster for this workload because 100 predictions were processed through a single HTTP request.

## 8. Canary and Rollback

A deliberately worse model was deployed as model version `1`. A second revision using model version `2` was used as the baseline.

The final canary used:

- `authv2`: 90% traffic
- `authv1`: 10% traffic

### Baseline: v2 only

At 10 VUs:

- Throughput: **15.78 req/s**
- p50: **608.08 ms**
- p95: **994.77 ms**
- Error rate: **0%**

### 90/10 Canary

At 10 VUs:

- Throughput: **7.68 req/s**
- p50: **601.32 ms**
- p95: **1311.90 ms**
- Error rate: **0%**

The canary showed a p95 increase of approximately **31.9%** relative to the same-environment v2-only baseline. The degradation was detected from aggregate latency metrics without using the reported model version.

Detection time: **60 s maximum**; the degradation was identified from aggregate latency metrics within the 60-second canary window, but the exact earlier onset was not timestamped.

### Rollback Evidence

Rollback was performed at:

`2026-09-29T22:30:12+07:00`

After rollback, traffic was confirmed as:

- `authv2`: **100%**

The final traffic configuration therefore moved all production traffic back to the v2 revision.

## 9. Cost Estimate

Azure Container Apps Consumption billing is based on allocated vCPU-seconds, GiB-seconds, and HTTP requests. The first 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million HTTP requests per subscription per month are free.

For the baseline `0.25 vCPU / 0.5 GiB` configuration, using active rates of approximately $0.000024 per vCPU-second and $0.000003 per GiB-second:

- Compute cost = $0.027/hour
- Measured throughput = 42.75 predictions/s
- Utilization assumption = 70%
- Estimated compute cost ≈ **$0.000251 per 1,000 predictions**

Including the HTTP request charge of $0.40 per million requests after the monthly free grant, the estimate is approximately **$0.000651 per 1,000 predictions**.

For the `0.5 vCPU / 1.0 GiB` configuration:

- Compute cost = $0.054/hour
- Measured throughput = 77.78 predictions/s
- Utilization assumption = 70%
- Estimated compute cost ≈ **$0.000276 per 1,000 predictions**
- Including request charges ≈ **$0.000676 per 1,000 predictions**

Using the measured **207 ms** for a 100-row batch, batch processing is estimated to become cheaper than keeping the `0.25 vCPU / 0.5 GiB` endpoint warm below approximately **1.74 million predictions/hour**, under the stated compute and request-charge assumptions.
