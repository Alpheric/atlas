export interface RouteResult {
  model: string;
  reason: string;
}

export const MODEL_DESCRIPTIONS: Record<string, string> = {
  "atlas-code": "Coding, debugging, architecture, code review",
  "atlas-plan": "Planning, reasoning, long-form thinking, architecture decisions",
  "atlas-secure": "Security review, threat modelling, vulnerability analysis",
  "atlas-infra": "DevOps, Kubernetes, Terraform, CI/CD, cloud infrastructure",
  "atlas-data": "SQL, analytics, data pipelines, ETL, databases",
  "atlas-books": "Writing, documentation, summaries, content",
  "atlas-audit": "Compliance, audit logs, governance, regulatory",
};

interface RoutingRule {
  model: string;
  reason: string;
  patterns: RegExp[];
}

const ROUTING_RULES: RoutingRule[] = [
  {
    model: "atlas-secure",
    reason: "Security-related task detected",
    patterns: [
      /\bsecurity\b/i,
      /\bvulnerabilit(y|ies)\b/i,
      /\bCVE\b/,
      /\bpentest\b/i,
      /\bpenetration.?test/i,
      /\bXSS\b/,
      /\bCSRF\b/,
      /\bexploit\b/i,
      /\binjection\b/i,
      /\bauth(entication|orization)?.?bypass/i,
    ],
  },
  {
    model: "atlas-plan",
    reason: "Planning or architecture task detected",
    patterns: [
      /\bplan\b/i,
      /\barchitecture\b/i,
      /\bdesign\b/i,
      /\broadmap\b/i,
      /\bstrategy\b/i,
      /\bbreakdown\b/i,
      /\bbreak.?down\b/i,
      /\bhigh.?level\b/i,
      /\bapproach\b/i,
      /\bproposal\b/i,
    ],
  },
  {
    model: "atlas-infra",
    reason: "Infrastructure or DevOps task detected",
    patterns: [
      /\bk8s\b/i,
      /\bkubernetes\b/i,
      /\bterraform\b/i,
      /\bdocker\b/i,
      /\bci\/cd\b/i,
      /\bcicd\b/i,
      /\bdevops\b/i,
      /\bnginx\b/i,
      /\bansible\b/i,
      /\bdeploy(ment)?\b/i,
      /\bhelm\b/i,
      /\bpod\b/i,
      /\bcontainer\b/i,
      /\bpipeline\b/i,
    ],
  },
  {
    model: "atlas-data",
    reason: "Data or analytics task detected",
    patterns: [
      /\bsql\b/i,
      /\bquery\b/i,
      /\bdatabase\b/i,
      /\betl\b/i,
      /\bpipeline\b/i,
      /\banalytics\b/i,
      /\bpandas\b/i,
      /\bdbt\b/i,
      /\bwarehouse\b/i,
      /\bschema\b/i,
      /\bmigration\b/i,
      /\bdataframe\b/i,
      /\bspark\b/i,
      /\bairflow\b/i,
    ],
  },
  {
    model: "atlas-books",
    reason: "Writing or documentation task detected",
    patterns: [
      /\bdocument\b/i,
      /\breadme\b/i,
      /\bdocs\b/i,
      /\bwrite\b/i,
      /\bdraft\b/i,
      /\bsummariz(e|ation)\b/i,
      /\bexplain\b/i,
      /\bblog\b/i,
      /\bcontent\b/i,
      /\bnarrative\b/i,
      /\bcopywrite\b/i,
    ],
  },
  {
    model: "atlas-audit",
    reason: "Compliance or governance task detected",
    patterns: [
      /\bcomplian(ce|t)\b/i,
      /\baudit\b/i,
      /\bgdpr\b/i,
      /\bhipaa\b/i,
      /\bsox\b/i,
      /\bregulat(ion|ory)\b/i,
      /\bgovernance\b/i,
      /\bpolicy\b/i,
      /\bpci.?dss\b/i,
      /\biso.?27001\b/i,
    ],
  },
  {
    model: "atlas-code",
    reason: "Coding or debugging task detected",
    patterns: [
      /\bcode\b/i,
      /\bdebug\b/i,
      /\bbuild\b/i,
      /\bfix\b/i,
      /\brefactor\b/i,
      /\bimplement\b/i,
      /\btest\b/i,
      /\bfunction\b/i,
      /\bclass\b/i,
      /\bmodule\b/i,
      /\bapi\b/i,
      /\berror\b/i,
      /\bbug\b/i,
    ],
  },
];

export function autoRouteModel(userMessage: string, defaultModel: string): RouteResult {
  for (const rule of ROUTING_RULES) {
    if (rule.patterns.some((re) => re.test(userMessage))) {
      return { model: rule.model, reason: rule.reason };
    }
  }
  return {
    model: defaultModel,
    reason: `Using default model (${defaultModel})`,
  };
}
