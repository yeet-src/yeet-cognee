const basename = (token) => token.slice(token.lastIndexOf("/") + 1);

const tokens = (proc) => [proc?.exe, proc?.comm, proc?.status?.name, ...(proc?.cmdline ?? [])].filter(Boolean);

export const matches = (proc, needles) => needles.some((n) => tokens(proc).some((t) => t === n || basename(t).startsWith(n)));

export const watched = (proc, watch, self) => !matches(proc, self) && matches(proc, watch);
