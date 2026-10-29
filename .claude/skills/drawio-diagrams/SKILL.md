---
name: drawio-diagrams
description: Draw or edit an architecture diagram of NODO AI in draw.io with the official Azure icons, so that it is clear and matches the real architecture (Terraform and code). Use whenever the user asks for a diagram, a picture of the architecture, a LinkedIn or README image, or edits a .drawio file.
---

# NODO AI diagrams in draw.io

The rules live in `docs/DIAGRAMS.md`. Read it before drawing, then follow it. In short:

1. **The `.drawio` is the source.** Edit it; never write a Python generator for a diagram. The PNG is an
   export (`node tools/diagrams/render_drawio.mjs in.drawio out.png`).
2. **Verify every label against the code** before drawing it: resources in
   `infra/terraform/modules/nodo/*.tf`, scaling in `apps.tf`/`voice.tf`, recovery targets in `dr.tf`,
   behaviour in `services/api` and `docs/adr`. Do not draw from the README alone.
3. **No status wording in the picture** (not deployed, without a subscription, target). Status belongs
   in the README. Never write Claude or Anthropic anywhere.
4. **Structure**: public edge → trust boundary (red dashed) → private network with lanes → standby
   region (RTO/RPO) → observability and delivery. Optional parts in a dashed box. A legend whenever
   something is dashed. Scaling and HA on the box.
5. **Official Azure icons only**, 72 px, each with a label; icons 20 px or more inside their group; no
   text below 12 px; arrows orthogonal and never through an icon.
6. **Run the check, then look at the PNG** at full size, region by region:
   `python tools/diagrams/check_drawio.py <file>.drawio` (it also runs automatically after a write).
7. After a change, update the README and `docs/AZURE-ARCHITECTURE.md` where they describe the picture.
