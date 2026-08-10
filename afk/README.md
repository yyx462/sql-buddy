# AFK harness moved

Production AFK for this repo is the **shared** harness:

| | |
|--|--|
| Harness repo | `ying.yuxiang/afk-home` |
| Jenkins job | `algo-afk` |
| Webhook | `https://cicd.nint.hk/generic-webhook-trigger/invoke?token=algo-afk` |
| Setup | afk-home `docs/SETUP-GUIDE.md` |
| Product ADR | [`docs/adr/0005-afk-consumer-park-review.md`](../docs/adr/0005-afk-consumer-park-review.md) |

This `afk/` directory is **legacy** (pre multi-repo split). Do not add a
product `Jenkinsfile.afk`. Labels on this repo: `ready-for-agent`,
`afk:in-progress`.
