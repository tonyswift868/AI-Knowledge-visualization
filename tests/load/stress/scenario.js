import { runEnterpriseScenario, setup, summary } from "../lib/core.js";
import { buildOptions } from "../lib/thresholds.js";

export const options = buildOptions("stress");
export { setup };
export default function (data) { runEnterpriseScenario(data, "stress"); }
export function handleSummary(data) { return summary(data, "stress"); }
