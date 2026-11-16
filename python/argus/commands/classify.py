"""Turn one shell command into what the ledger stores: cleaned, capped, secrets masked, risks named.

Pure: no I/O, no SQL, and the command is never executed or handed to a shell. Commands come
from model output, so they are treated as hostile text:

- every pattern is linear in its input (plain regexes with no nested or leading wildcards;
  risk rules walk whitespace tokens), and only the first ``SCAN_CHARS`` are looked at;
- secrets are masked *before* the length cap, so a cap can never store half a key;
- if masking raises, nothing of the raw text is kept (fail closed);
- control characters and text-direction overrides are replaced, so stored text cannot drive
  a terminal or disguise itself in a browser.

The rules match text. They do not understand the command: a variable, a script file, an
alias or code handed to an interpreter can hide a deletion from them, and ``echo rm -rf /``
is flagged though it deletes nothing. A flag means "worth a look", never "this was harmful".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_COMMAND_CHARS = 8_192     # stored text; real commands: median 283 bytes, p99 3.6 KB
SCAN_CHARS = 16_384           # how much of a command is read at all
UNREADABLE = "[redacted: command could not be read safely]"
_SEVERITY = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Risk:
    rule: str
    severity: str   # low | medium | high


@dataclass(frozen=True)
class CommandRecord:
    text: str
    truncated: bool = False
    secrets: tuple[str, ...] = ()   # kinds of secret found (masked in `text`), or "env-file"
    risks: tuple[Risk, ...] = ()

    @property
    def severity(self) -> str | None:
        return max((r.severity for r in self.risks), key=_SEVERITY.__getitem__, default=None)


# ---- cleaning ---------------------------------------------------------------------------

# C0 and C1 controls except tab and newline, DEL, and the bidi embedding/override/isolate marks.
_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‎‏‪-‮⁦-⁩]")


def _clean(text: str) -> str:
    return _UNSAFE.sub("�", text.replace("\r\n", "\n"))


# ---- secrets ----------------------------------------------------------------------------

def _mark(kind: str) -> str:
    return f"[redacted:{kind}]"


_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]{0,30}PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]{0,30}PRIVATE KEY-----|\Z)")
# Shapes providers publish for their own keys. Each is a fixed prefix then one character class.
_PROVIDER = [
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("api-key", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{30,}"
                           r"|pypi-[A-Za-z0-9_-]{40,}|glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})")),
    ("aws-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
]
_AUTH = re.compile(r"(?i)\b((?:authorization\s*[:=]\s*)?(?:bearer|basic)\s+)([A-Za-z0-9._~+/=-]{16,})")
_URL_PASSWORD = re.compile(r"\b([a-z][a-z0-9+.-]{1,15}://[^\s/:@]{1,80}:)([^\s/@]{3,200})(@)")
_FLAG = re.compile(r"(?i)(--(?:token|password|passwd|api-key|apikey|secret|auth-token|access-token|client-secret)(?:=|\s+))"
                   r"(\"[^\"\n]{1,400}\"|'[^'\n]{1,400}'|[^\s'\"]{1,400})")
# A name that says it holds a secret. Found first, then widened by hand: a regex with a
# wildcard before the keyword would rescan the same text from every start position.
_SECRET_WORD = re.compile(r"(?i)token|secret|passw(?:or)?d|api[_-]?key|private[_-]?key|access[_-]?key|credential")
_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
_ASSIGNED = re.compile(r"""["']?\s{0,3}[=:]\s{0,3}(?:"([^"\n]{0,400})"|'([^'\n]{0,400})'|([^\s'"&|;,)}\]]{1,400}))""")
_ENV_FILE = re.compile(r"(?:^|[\s/\\'\"=])\.env(?:\.(?!example\b|sample\b|template\b|dist\b)[A-Za-z0-9_-]{1,30})?(?=$|[\s'\";|&)])")
_ENV_READER = re.compile(r"(?i)\b(?:cat|type|get-content|gc|less|more|head|tail|grep|rg|select-string|source|bat)\b")


def _is_literal(value: str) -> bool:
    """A real value, as opposed to a variable reference, a placeholder or a number."""
    v = value.strip("'\"")
    return (len(v) >= 6 and v[0] not in "$%<{[(" and not v.isdigit()
            and not v.lower().startswith(("true", "false", "null", "none", "redacted")))


def _mask_assignments(text: str, found: set[str]) -> str:
    out, last = [], 0
    for m in _SECRET_WORD.finditer(text):
        if m.start() < last:
            continue
        end = m.end()
        while end < len(text) and end - m.end() < 64 and text[end] in _NAME_CHARS:
            end += 1
        a = _ASSIGNED.match(text, end)
        if not a:
            continue
        group = next(i for i in (1, 2, 3) if a.group(i) is not None)
        value = a.group(group)
        if not _is_literal(value):
            continue
        out.append(text[last:a.start(group)])
        out.append(_mark("assignment"))
        last = a.end(group)
        found.add("assignment")
    out.append(text[last:])
    return "".join(out)


def _mask(text: str) -> tuple[str, tuple[str, ...]]:
    """`text` with every secret-looking value replaced by a marker, and the kinds found."""
    found: set[str] = set()

    def swap(kind: str, keep_before: int = 0, value: int = 0, keep_after: int = 0):
        def repl(m: re.Match) -> str:
            if value and not _is_literal(m.group(value)):
                return m.group(0)
            found.add(kind)
            return (m.group(keep_before) if keep_before else "") + _mark(kind) + (m.group(keep_after) if keep_after else "")
        return repl

    text = _PRIVATE_KEY.sub(swap("private-key"), text)
    for kind, rx in _PROVIDER:
        text = rx.sub(swap(kind), text)
    text = _AUTH.sub(swap("auth-header", 1, 2), text)
    text = _URL_PASSWORD.sub(swap("url-password", 1, 2, 3), text)
    text = _FLAG.sub(swap("flag", 1, 2), text)
    text = _mask_assignments(text, found)
    if _ENV_FILE.search(text) and _ENV_READER.search(text):
        found.add("env-file")   # the command holds no secret; its output very likely did
    return text, tuple(sorted(found))


# ---- risks ------------------------------------------------------------------------------

_SPLIT = re.compile(r"\n|;|&&|\|\||&")
_SQL_DROP = re.compile(r"(?i)\b(?:drop\s{1,5}(?:table|database|schema)|truncate\s{1,5}table)\b")
_SQL_DELETE = re.compile(r"(?i)\bdelete\s{1,5}from\b")
_DOWNLOADERS = frozenset({"curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "iex", "invoke-expression"})
_RM_PS = frozenset({"remove-item", "ri", "rd", "rmdir", "del", "erase"})
# Deleting these (or everything under them) reaches outside any one project.
_WIDE_TARGET = re.compile(
    r"""(?ix)^(?:
        [a-z]:[\\/]*\*?                                   # a drive root
      | [\\/]\*?                                          # the filesystem root
      | ~[\\/]?\*?                                        # home
      | \$\{?home\}?[\\/]?\*?  | \$env:userprofile[\\/]?\*?  | %userprofile%[\\/]?\*?
      | \.\.(?:[\\/].*)?                                  # anything above the current folder
      | /(?:etc|usr|var|bin|sbin|lib|boot|opt|home|root|sys|dev)(?:/.*)?
      | [a-z]:[\\/](?:windows|users|program\ files[^\\/]*)(?:[\\/][^\\/]*)?[\\/]?
    )$""")


_MAX_ARGS = 48
_WATCHED = frozenset({"git", "rm", "sudo", "kubectl", "docker", "helm", "terraform", "tofu", "chmod", "npm", "pnpm",
                      "yarn", "uv", "cargo", "twine", "gh", "dd", "diskpart"}) | _RM_PS


def _word(token: str) -> str:
    """A token as a command name: quotes and brackets off, folder and .exe dropped, lower-cased."""
    t = token.strip("'\"()`{}").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return t[:-4] if t.endswith(".exe") else t


def _git(args: list[str]) -> list[Risk]:
    """Risks of one `git ...` invocation; `args` are the tokens after `git`."""
    i = 0
    while i < len(args) and args[i].startswith("-"):      # global options: -C <dir>, -c <k=v>
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
    if i >= len(args):
        return []
    sub, rest = args[i].lower(), [a.strip("'\"") for a in args[i + 1:]]
    out: list[Risk] = []
    if sub == "push":
        if "--force" in rest or "-f" in rest or any(r.startswith("+") and len(r) > 1 for r in rest):
            out.append(Risk("git-force-push", "high"))
        elif any(r.startswith("--force-with-lease") or r == "--force-if-includes" for r in rest):
            out.append(Risk("git-force-push", "medium"))
    elif sub == "reset" and "--hard" in rest:
        out.append(Risk("git-reset-hard", "medium"))
    elif sub == "clean" and any(r.startswith("-") and not r.startswith("--") and "f" in r or r == "--force" for r in rest):
        out.append(Risk("git-clean", "medium"))
    elif sub == "checkout" and ("--" in rest or "." in rest):
        out.append(Risk("git-discard", "low"))
    elif sub == "restore" and "--staged" not in rest and "-S" not in rest:
        out.append(Risk("git-discard", "low"))
    elif sub == "branch" and ("-D" in rest or ("--delete" in rest and "--force" in rest)):
        out.append(Risk("git-branch-delete", "low"))
    elif sub in ("filter-branch", "filter-repo"):
        out.append(Risk("git-rewrite-history", "high"))
    if sub in ("commit", "push", "merge") and ("--no-verify" in rest or (sub == "commit" and "-n" in rest)):
        out.append(Risk("skips-hooks", "low"))
    return out


def _delete(args: list[str], powershell: bool) -> Risk | None:
    """A recursive, forced delete; `args` are the tokens after the command."""
    flags = [a.lower() for a in args if a.startswith("-") or (powershell and a.startswith("/"))]
    targets = [a.strip("'\"") for a in args if not a.startswith("-") and not (powershell and a.startswith("/") and len(a) == 2)]
    if powershell:
        # PowerShell accepts any unambiguous prefix of a parameter name (-r, -rec, -fo); cmd uses /s /q.
        recurse = any((f.startswith("-r") and "recurse".startswith(f[1:])) or f == "/s" for f in flags)
        force = any((f.startswith("-f") and "force".startswith(f[1:])) or f == "/q" for f in flags)
    else:
        short = "".join(f[1:] for f in flags if not f.startswith("--"))
        recurse = "r" in short or "--recursive" in flags
        force = "f" in short or "--force" in flags
    if not (recurse and force):
        return None
    return Risk("delete-recursive", "high" if any(_WIDE_TARGET.match(t) for t in targets) else "low")


_HEREDOC = re.compile(r"<<-?[ \t]{0,3}(['\"]?)([A-Za-z_][A-Za-z0-9_]{0,40})\1")
_MAX_HEREDOCS = 64
_HEREDOC_SHELLS = _SHELLS | {"ssh"}


def _shell_text(text: str) -> str:
    """`text` without the bodies of here-documents that feed something other than a shell.

    `python - <<'EOF' ... EOF` and `cat > f.sql <<EOF ... EOF` hand text to a program or a
    file; reading that text as shell commands flagged scripts that merely contained the
    words `DROP TABLE` or `mkfs`. A here-document fed to sh/bash/ssh is kept. Past
    `_MAX_HEREDOCS` the rest is left as it is (and so still read): bounded work, and the
    cautious side.
    """
    out, pos = [], 0
    for _ in range(_MAX_HEREDOCS):
        m = _HEREDOC.search(text, pos)
        if not m:
            break
        body = text.find("\n", m.end())
        if body < 0:
            break
        end = re.compile(r"\n[ \t]*" + re.escape(m.group(2)) + r"[ \t]*(?=\n|$)").search(text, body)
        after = end.end() if end else len(text)
        line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        consumer = _SPLIT.split(line)[-1].split("|")[-1]
        if any(_word(t) in _HEREDOC_SHELLS for t in consumer.split()):
            out.append(text[pos:after])
        else:
            out.append(text[pos:body])
        pos = after
    out.append(text[pos:])
    return "".join(out)


def _risks(text: str) -> tuple[Risk, ...]:
    text = _shell_text(text)
    found: dict[str, str] = {}

    def add(r: Risk | None) -> None:
        if r and _SEVERITY[r.severity] >= _SEVERITY.get(found.get(r.rule, ""), -1):
            found[r.rule] = r.severity

    if _SQL_DROP.search(text):
        add(Risk("sql-destructive", "high"))
    elif _SQL_DELETE.search(text) and not re.match(r"\s*(?:grep|rg|select-string|findstr)\b", text, re.I):
        add(Risk("sql-destructive", "medium"))
    if found.get("sql-destructive") == "high" and re.match(r"\s*(?:grep|rg|select-string|findstr)\b", text, re.I):
        del found["sql-destructive"]   # searching for the words is not running them
    for segment in _SPLIT.split(text):
        stages = [s.split() for s in segment.split("|")]
        downloading = False
        for tokens in stages:
            words = [_word(t) for t in tokens]
            if downloading and any(w in _SHELLS for w in words[:3]):
                add(Risk("pipe-to-shell", "high"))
            downloading = downloading or any(w in _DOWNLOADERS for w in words)
            if words[:1] in (["iex"], ["invoke-expression"]) and any(w in _DOWNLOADERS for w in words[1:]):
                add(Risk("pipe-to-shell", "high"))
            for i, w in enumerate(words):
                if w not in _WATCHED and not w.startswith("mkfs"):
                    continue
                # A bounded look ahead: slicing to the end for every word would make a long
                # run of `git git git ...` cost the square of its length.
                args = tokens[i + 1:i + 1 + _MAX_ARGS]
                low = [a.lower().strip("'\"") for a in args]
                if w == "git":
                    for r in _git(args):
                        add(r)
                elif w == "rm":
                    add(_delete(args, powershell=False))
                elif w in _RM_PS:
                    add(_delete(args, powershell=True))
                elif w == "sudo":
                    add(Risk("sudo", "medium"))
                elif w == "kubectl" and "delete" in low[:3]:
                    add(Risk("infra-delete", "medium"))
                elif w == "docker" and ({"rm", "rmi", "prune"} & set(low[:3])):
                    add(Risk("infra-delete", "medium"))
                elif w == "helm" and ({"uninstall", "delete"} & set(low[:2])):
                    add(Risk("infra-delete", "medium"))
                elif w in ("terraform", "tofu") and "destroy" in low[:3]:
                    add(Risk("infra-delete", "medium"))
                elif w == "chmod" and any(a in ("777", "a+rwx", "0777") for a in low):
                    add(Risk("chmod-open", "medium"))
                elif (w in ("npm", "pnpm", "yarn", "uv", "cargo") and low[:1] == ["publish"]) or \
                        (w == "twine" and low[:1] == ["upload"]) or (w == "gh" and low[:2] == ["release", "create"]):
                    add(Risk("publish", "medium"))
                elif (w == "dd" and any(a.startswith("of=/dev/") for a in low)) or w.startswith("mkfs") or w == "diskpart":
                    add(Risk("disk-write", "high"))
    return tuple(Risk(rule, sev) for rule, sev in sorted(found.items()))


# ---- the one entry point ------------------------------------------------------------------

def classify(command: object) -> CommandRecord:
    """What to store for `command`. Never raises; anything that is not text is an empty record."""
    if not isinstance(command, str) or not command:
        return CommandRecord("")
    truncated = len(command) > SCAN_CHARS
    try:
        text, secrets = _mask(_clean(command[:SCAN_CHARS]))
    except Exception:  # noqa: BLE001  fail closed: an unmasked command is never stored
        return CommandRecord(UNREADABLE, truncated, ("unreadable",))
    try:
        risks = _risks(text)
    except Exception:  # noqa: BLE001  a rule that breaks loses its flag, not the ingest
        risks = ()
    if len(text) > MAX_COMMAND_CHARS:
        text, truncated = text[:MAX_COMMAND_CHARS], True
    return CommandRecord(text, truncated, secrets, risks)
