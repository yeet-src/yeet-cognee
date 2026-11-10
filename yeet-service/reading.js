/* The HTTP face of the sysgraph reader: one portal, one job.
 *
 *   GET /                         a complete host reading, JSON
 *   GET /reading?watch=coder.py   the same, with that workload pulled in full
 *   GET /reading?watch=a,b&top=8  several workloads, more background processes
 *   GET /activity?watch=coder.py&since=<ms>   what those workloads did since then:
 *                                 every exec, open and connect, children included
 *
 * A reading is kernel ground truth assembled into one JSON object: whole-box
 * state (load, memory, GPU), each watched process in full (fds, io, sockets
 * attributed by inode), and the largest other processes. The activity log
 * is the same truth over time: the actions a reading cannot show because
 * they were over before it was taken. No interpretation -- the cognee side
 * turns both into prose and judges them.
 *
 * The hostname is passed in from service.toml (`--hostname <name>`) so every
 * reading is stamped with the box's own name, which is what lets cognee link
 * observations of the same host across time.
 */

import { activity, start as startActivityLog } from "./lib/activity.js";
import { reading } from "./lib/assemble.js";

const args = yeet.args ?? {};
const HOSTNAME = args.hostname ?? null;
// The sentinel's own processes, and anything that merely names a workload
// in its command line (the curl that asks about `agent-04` is not agent-04).
const DEFAULT_SELF = ["sentinel.", "-m sentinel", "scenario/fleet.py", "demo.sh", "/ask", "/remember", "/activity", "/reading"];

function parse(url) {
  const [path, qs = ""] = String(url ?? "/").split("?", 2);
  const params = new Map();
  for (const pair of qs.split("&")) {
    if (!pair) continue;
    const [k, v = ""] = pair.split("=", 2);
    params.set(decodeURIComponent(k), decodeURIComponent(v.replace(/\+/g, " ")));
  }
  return { path: path.replace(/\/+$/, "") || "/", params };
}

function list(value) {
  return value ? value.split(",").map((s) => s.trim()).filter(Boolean) : [];
}

async function respond(peer, url) {
  const { path, params } = parse(url);
  if (path === "/activity") {
    const since = params.has("since") ? Number(params.get("since")) : Date.now() - 60_000;
    const until = params.has("until") ? Number(params.get("until")) : Infinity;
    if (!Number.isFinite(since) || Number.isNaN(until)) throw new Error("since/until must be epoch milliseconds");
    return peer.write(JSON.stringify(await activity({
      watch: list(params.get("watch")),
      self: [...DEFAULT_SELF, ...list(params.get("self"))],
      since, until,
      limit: params.has("limit") ? Number(params.get("limit")) : 2000,
    })));
  }
  if (path !== "/" && path !== "/reading") {
    return peer.write(`no such route: ${path}\nroutes: / /reading (?watch=substr,substr ?self=substr ?top=N) /activity (?watch= ?since=ms ?until=ms ?limit=N)\n`);
  }
  const opts = {
    watch: list(params.get("watch")),
    self: list(params.get("self")),
    hostname: params.get("hostname") ?? HOSTNAME,
    topOther: params.has("top") ? Number(params.get("top")) : 5,
  };
  if (!Number.isInteger(opts.topOther) || opts.topOther < 0) {
    throw new Error("top must be a non-negative integer");
  }
  peer.write(JSON.stringify(await reading(opts)));
}

await startActivityLog();

const portal = new Portal("http");
portal.on("peer", async (peer, request) => {
  try {
    await respond(peer, request?.url);
  } catch (error) {
    if (!peer.closed) peer.write(JSON.stringify({ error: error?.message ?? String(error) }) + "\n");
  } finally {
    if (!peer.closed) peer.close();
  }
});

console.log(`sysgraph: serving /, /reading and /activity on portal http (hostname: ${HOSTNAME ?? "unset"})`);
