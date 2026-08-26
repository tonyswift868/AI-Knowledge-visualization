import { runEnterpriseScenario, setup, summary } from "../lib/core.js";
import { buildOptions } from "../lib/thresholds.js";

export const options = buildOptions("tenant-isolation");
export { setup };
export default function (data) { runEnterpriseScenario(data, "tenant-isolation"); }
export function handleSummary(data) { return summary(data, "tenant-isolation"); }
