/**
 * AFK entry: resolve one Gitea issue → sandcastle.run → wrapper opens PR.
 * Harness files live under afk/; Sandcastle cwd defaults to repo root.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { run, claudeCode, pi, type AgentProvider } from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { noSandbox } from "@ai-hero/sandcastle/sandboxes/no-sandbox";

const DEFAULT_DOCKER_IMAGE = "sql-buddy-afk:pi";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const afkRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(afkRoot, "..");
const teaSh = path.join(afkRoot, "scripts/tea.sh");

function loadEnvFile(p: string) {
  if (!existsSync(p)) return;
  for (const line of readFileSync(p, "utf8").split("\n")) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const i = t.indexOf("=");
    if (i < 0) continue;
    const k = t.slice(0, i).trim();
    let v = t.slice(i + 1).trim();
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1);
    }
    if (process.env[k] === undefined) process.env[k] = v;
  }
}

loadEnvFile(path.join(__dirname, ".env"));

function sh(
  cmd: string,
  args: string[],
  opts: { cwd?: string; env?: NodeJS.ProcessEnv; allowFail?: boolean } = {},
): string {
  const r = spawnSync(cmd, args, {
    encoding: "utf8",
    cwd: opts.cwd ?? repoRoot,
    env: { ...process.env, ...opts.env },
  });
  if (r.status !== 0 && !opts.allowFail) {
    throw new Error(
      `${cmd} ${args.join(" ")} failed:\n${r.stderr || r.stdout}`,
    );
  }
  return (r.stdout || "").trim();
}

function tea(args: string[], opts: { allowFail?: boolean } = {}): string {
  return sh("bash", [teaSh, ...args], opts);
}

type Issue = {
  number: number;
  title: string;
  body: string;
  url: string;
};

function parseIssueJson(raw: string): Issue {
  const data = JSON.parse(raw);
  // tea may return object or single-element array depending on version/command
  const obj = Array.isArray(data) ? data[0] : data;
  if (!obj) throw new Error("empty issue payload from tea");
  const number = Number(obj.index ?? obj.number ?? obj.id);
  if (!Number.isFinite(number)) {
    throw new Error(`cannot parse issue number from: ${raw.slice(0, 200)}`);
  }
  return {
    number,
    title: String(obj.title ?? ""),
    body: String(obj.body ?? obj.description ?? ""),
    url: String(obj.url ?? obj.html_url ?? ""),
  };
}

function resolveIssue(): Issue {
  tea(["ensure-login"]);
  const n = process.env.AFK_ISSUE;
  if (n) {
    return parseIssueJson(tea(["issue-view", n]));
  }
  const listRaw = tea(["issue-list-ready"]);
  const arr = JSON.parse(listRaw);
  const first = Array.isArray(arr) ? arr[0] : arr;
  if (!first) {
    throw new Error(
      `No open issues with label ${process.env.AFK_LABEL || "ready-for-agent"}`,
    );
  }
  return parseIssueJson(JSON.stringify(first));
}

function branchExistsOnOrigin(branch: string, cwd: string): boolean {
  const r = spawnSync(
    "git",
    ["ls-remote", "--exit-code", "--heads", "origin", branch],
    { cwd, encoding: "utf8" },
  );
  return r.status === 0;
}

function q(s: string) {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

function piWithProvider(
  modelId: string,
  provider: string,
  env: Record<string, string>,
): AgentProvider {
  const base = pi(modelId, { env });
  return {
    ...base,
    env: { ...base.env, ...env },
    buildPrintCommand(opts) {
      const thinking = process.env.AFK_THINKING
        ? ` --thinking ${q(process.env.AFK_THINKING)}`
        : "";
      const session = opts.resumeSession
        ? ` --session ${q(opts.resumeSession)}`
        : "";
      return {
        command: `pi -p --mode json --provider ${q(provider)} --model ${q(modelId)}${thinking}${session}`,
        stdin: opts.prompt,
      };
    },
    buildInteractiveArgs(opts) {
      const args = ["pi", "--provider", provider, "--model", modelId];
      if (opts.prompt) args.push(opts.prompt);
      return args;
    },
  };
}

function pickAgent(): AgentProvider {
  const kind = (process.env.AFK_AGENT || "pi").toLowerCase();
  const model =
    process.env.AFK_MODEL || (kind === "pi" ? "glm-4.7" : "claude-sonnet-4-6");
  const provider = process.env.AFK_PROVIDER || "zai-coding-cn";

  const zaiKey =
    process.env.ZAI_CODING_CN_API_KEY ||
    process.env.ZAI_API_KEY ||
    process.env.Z_AI_API_KEY ||
    "";
  const agentEnv: Record<string, string> = {};
  if (zaiKey) {
    agentEnv.ZAI_CODING_CN_API_KEY = zaiKey;
    agentEnv.ZAI_API_KEY = zaiKey;
  }
  if (process.env.ANTHROPIC_API_KEY)
    agentEnv.ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

  if (kind === "pi") {
    if (!zaiKey) {
      console.warn(
        "Warning: no Coding Plan key (ZAI_CODING_CN_API_KEY / ZAI_API_KEY)",
      );
    }
    console.log(`agent: pi --provider ${provider} --model ${model}`);
    return piWithProvider(model, provider, agentEnv);
  }
  return claudeCode(model, { env: agentEnv });
}

function pickSandbox() {
  const mode = (process.env.AFK_SANDBOX || "docker").toLowerCase();
  if (mode === "none" || mode === "no" || mode === "host") {
    console.warn("AFK_SANDBOX=none — agent runs on host (debug only).");
    return noSandbox();
  }
  const imageName = process.env.AFK_DOCKER_IMAGE || DEFAULT_DOCKER_IMAGE;
  const mounts: Array<{
    hostPath: string;
    sandboxPath: string;
    readonly?: boolean;
  }> = [];
  const readonlyIni =
    process.env.AFK_READONLY_INI ||
    path.join(repoRoot, "connections/config/db.ini.example");
  if (existsSync(readonlyIni)) {
    mounts.push({
      hostPath: readonlyIni,
      sandboxPath: "/workspace/connections/config/db.ini.example",
      readonly: true,
    });
  }
  const piConfig = process.env.AFK_PI_CONFIG_DIR;
  if (piConfig) {
    mounts.push({
      hostPath: piConfig,
      sandboxPath: "/home/agent/.pi/agent",
      readonly: true,
    });
  }
  console.log(`sandbox: docker image=${imageName}`);
  return docker({
    imageName,
    ...(mounts.length ? { mounts } : {}),
  });
}

async function main() {
  const workDir = process.env.AFK_CWD
    ? path.resolve(process.env.AFK_CWD)
    : repoRoot;

  const issue = resolveIssue();
  console.log(`AFK: #${issue.number} ${issue.title}`);
  if (issue.url) console.log(issue.url);

  const branch = `afk/issue-${issue.number}`;

  // Idempotency: branch or PR already exists → do not re-run agent
  const prExists =
    tea(["pr-exists-for-branch", branch], { allowFail: true }) === "yes";
  if (prExists) {
    console.log(`PR already open for ${branch} — nothing to do`);
    tea(["finish-claim", String(issue.number)], { allowFail: true });
    return;
  }
  if (branchExistsOnOrigin(branch, workDir)) {
    console.log(
      `origin already has ${branch} — open-pr only (no agent re-run)`,
    );
    const openPr = path.join(afkRoot, "scripts/open-pr.sh");
    sh("bash", [openPr, String(issue.number), branch, issue.title], {
      cwd: workDir,
    });
    return;
  }

  const promptPath = path.join(__dirname, "prompt.md");
  const filledPath = path.join(__dirname, ".prompt.filled.md");
  let prompt = readFileSync(promptPath, "utf8");
  prompt = prompt
    .replaceAll("{{ISSUE_NUMBER}}", String(issue.number))
    .replaceAll("{{ISSUE_TITLE}}", issue.title)
    .replaceAll("{{ISSUE_BODY}}", issue.body || "")
    .replaceAll("{{ISSUE_URL}}", issue.url);
  writeFileSync(filledPath, prompt);

  if (process.env.AFK_DRY_RUN === "1") {
    console.log("AFK_DRY_RUN=1 — wrote filled prompt only:");
    console.log(filledPath);
    console.log(prompt.slice(0, 600) + (prompt.length > 600 ? "…" : ""));
    return;
  }

  tea(["claim", String(issue.number)]);

  try {
    const result = await run({
      agent: pickAgent(),
      sandbox: pickSandbox(),
      cwd: workDir,
      branchStrategy: { type: "branch", branch },
      promptFile: filledPath,
      maxIterations: Number(process.env.AFK_MAX_ITERATIONS || 8),
    });

    console.log(
      `sandcastle done: branch=${result.branch} commits=${result.commits.length}`,
    );
    if (!result.commits.length) {
      console.warn("No commits — releasing claim, skipping PR.");
      tea(["release-claim", String(issue.number)], { allowFail: true });
      process.exitCode = 2;
      return;
    }

    const openPr = path.join(afkRoot, "scripts/open-pr.sh");
    sh("bash", [openPr, String(issue.number), branch, issue.title], {
      cwd: workDir,
    });
  } catch (e) {
    tea(["release-claim", String(issue.number)], { allowFail: true });
    throw e;
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
