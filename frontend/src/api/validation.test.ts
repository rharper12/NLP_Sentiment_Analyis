import { describe, expect, it } from "vitest";

import { datasetSummarySchema, labelEstimateSchema } from "./validation";

const summary = {
  dataset_id: "abc123",
  source_type: "x",
  query: "\"lindsay clancy\" lang:en",
  record_count: 512,
  labelled_count: 0,
  truncated_reason: null,
  preview: [],
};

describe("response validation", () => {
  it("accepts a well-formed response", () => {
    expect(datasetSummarySchema.parse(summary)).toMatchObject({ record_count: 512 });
  });

  it("tolerates fields a newer API added, so an older UI keeps working", () => {
    const parsed = datasetSummarySchema.parse({ ...summary, some_future_field: true });
    expect(parsed.dataset_id).toBe("abc123");
  });

  it("rejects a missing field instead of letting undefined reach a component", () => {
    const withoutCount = Object.fromEntries(
      Object.entries(summary).filter(([key]) => key !== "record_count"),
    );
    const result = datasetSummarySchema.safeParse(withoutCount);
    expect(result.success).toBe(false);
    expect(result.error?.issues[0].path).toEqual(["record_count"]);
  });

  it("rejects a field of the wrong type", () => {
    const result = datasetSummarySchema.safeParse({ ...summary, record_count: "512" });
    expect(result.success).toBe(false);
  });

  it("keeps null distinct from missing on the money path", () => {
    // `estimated_cost_usd: null` means "no current price", which the UI must show as
    // "estimate unavailable" rather than $0.00 — so null has to parse, and be preserved.
    const estimate = {
      records_total: 600, records_unlabelled: 600, records_to_send: 600, billable_units: 1800,
      unit_chars: 100, min_units_per_document: 3, estimated_cost_usd: null,
      cost_per_unit_usd: null, price_status: "unavailable", price_fetched_at: null,
      price_region: "us-east-1",
    };
    expect(labelEstimateSchema.parse(estimate).estimated_cost_usd).toBeNull();
    expect(labelEstimateSchema.safeParse({ ...estimate, price_status: "guessed" }).success).toBe(false);
  });
});
