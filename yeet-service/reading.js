import { activity, enforceStatus, setEnforcement, start as startActivityLog } from "./lib/activity.js";
import { reading } from "./lib/assemble.js";

const HOSTNAME = yeet.args?.hostname ?? null;
const SELF = ["sentinel", "fleet.py", "demo.sh"];
const ROUTES = "routes: / /reading /activity /enforce (?spec=<url-encoded json {on, watch, deny}>)";

function parse(url) {
  const [path, query = ""] = String(url ?? "/").split("?", 2);
  const params = new Map(
    query.split("&").filter(Boolean).map((pair) => {
      const [k, v = ""] = pair.split("=", 2);
      return [decodeURIComponent(k), decodeURIComponent(v.replace(/\+/g, " "))];
    }),
  );
  return { path: path.replace(/\/+$/, "") || "/", params };
}

const list = (value) => (value ? value.split(",").map((s) => s.trim()).filter(Boolean) : []);

const integer = (params, name, fallback) => {
  const value = params.has(name) ? Number(params.get(name)) : fallback;
  if (!Number.isInteger(value) || value < 0) throw new Error(`${name} must be a non-negative integer`);
  return value;
};

async function respond(peer, url) {
  const { path, params } = parse(url);
  if (path === "/enforce") {
    if (!params.has("spec")) return peer.write(JSON.stringify(await enforceStatus()));
    return peer.write(JSON.stringify(await setEnforcement(JSON.parse(params.get("spec")))));
  }
  if (path === "/activity") {
    return peer.write(JSON.stringify(await activity({
      watch: list(params.get("watch")),
      self: [...SELF, ...list(params.get("self"))],
      since: integer(params, "since", Date.now() - 60_000),
      until: params.has("until") ? integer(params, "until") : Infinity,
      limit: integer(params, "limit", 2000),
    })));
  }
  if (path === "/" || path === "/reading") {
    return peer.write(JSON.stringify(await reading({
      watch: list(params.get("watch")),
      self: [...SELF, ...list(params.get("self"))],
      hostname: params.get("hostname") ?? HOSTNAME,
      topOther: integer(params, "top", 5),
    })));
  }
  peer.write(`no such route: ${path}\n${ROUTES}\n`);
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
