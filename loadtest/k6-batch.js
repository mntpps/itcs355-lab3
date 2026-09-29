// ITCS355 Lab 3 — batch-size comparison.
//
// Compare:
//   1. one /predict/batch request containing 100 rows
//   2. 100 separate /predict requests
//
// Lab 3 requires reporting whether batching is faster and by how much.

import http from 'k6/http';
import { check } from 'k6';

const payload = {
  temp_c: 78.4,
  vibration_mm_s: 3.1,
  pressure_kpa: 315.2,
  hours_since_service: 4200,
  load_pct: 68.0,
  ambient_humidity: 55.0,
};

const rows = Array(100).fill(payload);

export const options = {
  vus: 1,
  iterations: 1,
};

export default function () {
  const batchPayload = JSON.stringify({ rows });

  const batchStart = Date.now();
  const batchRes = http.post(
    `${__ENV.TARGET}/predict/batch`,
    batchPayload,
    {
      headers: { 'Content-Type': 'application/json' },
    },
  );
  const batchLatency = Date.now() - batchStart;

  check(batchRes, {
    'batch status is 200': (r) => r.status === 200,
    'batch returned 100 probabilities': (r) =>
      r.status === 200 && r.json('probabilities').length === 100,
  });

  let singleLatency = 0;

  for (let i = 0; i < 100; i++) {
    const singleStart = Date.now();

    const res = http.post(
      `${__ENV.TARGET}/predict`,
      JSON.stringify(payload),
      {
        headers: { 'Content-Type': 'application/json' },
      },
    );

    singleLatency += Date.now() - singleStart;

    check(res, {
      'single status is 200': (r) => r.status === 200,
      'single probability present': (r) =>
        r.status === 200 && r.json('probability') !== undefined,
    });
  }

  console.log(`batch_100_rows_ms=${batchLatency}`);
  console.log(`single_100_calls_ms=${singleLatency}`);
}