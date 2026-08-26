import { runEnterpriseScenario, setup, summary } from "../lib/core.js";
import { buildOptions } from "../lib/thresholds.js";

export const options = buildOptions("smoke");
export { setup };
export default function (data) { runEnterpriseScenario(data, "smoke"); }
export function handleSummary(data) { return summary(data, "smoke"); }
