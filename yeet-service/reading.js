/* The HTTP face of the sysgraph reader: one portal, one job.
 *
 *   GET /                         a complete host reading, JSON
 *   GET /reading?watch=coder.py   the same, with that workload pulled in full
 *   GET /reading?watch=a,b&top=8  several workloads, more background processes
 *
 * A reading is kernel ground truth assembled into one JSON object: whole-box
 * state (load, memory, GPU), each watched process in full (fds, io, sockets
 * attributed by inode), and the largest other processes. No interpretation --
 * the cognee side turns this into prose and judges it.
 *
 * The hostname is passed in from service.toml (`--hostname <name>`) so every
 * reading is stamped with the box's own name, which is what lets cognee link
 * observations of the same host across time.
 */

import { reading } from "./lib/assemble.js";

const args = yeet.args ?? {};
const HOSTNAME = args.hostname ?? null;

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
  if (path !== "/" && path !== "/reading") {
    return peer.write(`no such route: ${path}\nroutes: / /reading (?watch=substr,substr ?self=substr ?top=N)\n`);
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

console.log(`sysgraph: serving / and /reading on portal http (hostname: ${HOSTNAME ?? "unset"})`);
