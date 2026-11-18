# Kubernetes Skill for Claude Code — KubeShark

KubeShark is a failure-mode-first Kubernetes skill for Claude Code and Codex. It prevents common LLM hallucinations in Kubernetes manifest generation by diagnosing risks before writing YAML.

## Why use it

- **Prevents hallucinations** -- 6 named failure modes with targeted reference files
- **Token-efficient** -- ~650 token activation cost, granular references loaded on demand
- **Production-ready defaults** -- Pod Security Standards restricted profile, proper resource management, cross-resource validation
- **20 reference files** -- covering security, networking, RBAC, probes, storage, Helm, Kustomize, and more

## Key features

- Failure-mode-first diagnostic workflow (diagnose before generate)
- Output contracts with assumptions, tradeoffs, and rollback notes
- LLM mistake checklists in every reference file
- Cross-resource consistency validation (label/selector/port alignment)
- Helm and Kustomize pattern guidance
- Policy engine integration (Kyverno, OPA/Gatekeeper)

## Quick install

```bash
git clone https://github.com/LukasNiessen/kubernetes-skill.git ~/.claude/skills/kubernetes-skill
```

## License

MIT -- see [LICENSE](https://github.com/LukasNiessen/kubernetes-skill/blob/main/LICENSE).

## Appendix Note
package-lock.json isn’t required for npm to function — npm install will quietly generate one on the fly if it’s missing — but there’s a good reason this specific repo (and almost every real project) commits it.

What it actually does:

- package.json only states loose ranges ("honkit": "^6.1.6"). That range matches any version from 6.1.6 up to (not including) 7.0.0.
- package-lock.json pins the exact resolved version of every package in the tree — direct and transitive — plus a SHA-512 integrity hash for each. It’s the difference between “something compatible with 6.1.6” and “exactly 6.2.6, verified byte-for-byte.”
- It’s what makes npm ci possible — the strict, reproducible install mode CI pipelines use. npm ci refuses to run at all without a lockfile.

Why that matters here, concretely:

- Without a committed lockfile, two people running npm install on the same package.json, a week apart, can end up with different transitive dependency versions — because the registry keeps publishing new releases that satisfy the same range. Your build is no longer the same build.
- This is also literally why Dependabot could find and I could fix these alerts. Dependabot scans the lockfile’s exact resolved versions, not package.json’s ranges — package.json alone doesn’t tell you whether you’re running the vulnerable undici or the patched one, only the lockfile does.
- It’s also a supply-chain safeguard: the integrity hash means a compromised or tampered package published under the same version number would fail the install rather than silently substituting in.
