# MK1 variant handoff for an AI agent

Read README.md, docs/WORKFLOW.md, docs/FIRMWARE.md and docs/HISTORY.md first.
This is the MK1-3290-V2 / AX3291A camera, not the AX3295B penguin. These addresses
apply to the exact stock dump with SHA-256
33ac2db5716dacf06b60f97c5efbb2b2b77d820fe2b13e58c1eeb4379094fab5.

## Owner's product decision

Keep the menu, SD-card capture, settings, printing toggle and other stock
functions: the children who own this camera want them. Keep the original upper
information bar. Show only frames 1–7, plus the three requested dithering modes;
labels are screen-only white text with a transparent background below the bar.
Do not carry over the penguin's removal of storage/menu functions or its controls.
For a new owner, clarify their requested customization before changing behaviour.

## Hardware actions

- Offline docs/builds/tests do not authorize flash writes.
- Ask the human before **each** writing phase, naming the image, SHA-256, sectors
  and purpose. A flag such as --approved-phase records a prior approval; it does
  not grant approval by itself. Ask once per proposed phase, not per SPI page.
- Never set autoResolutionMs when asking for input. Wait for required approval.
- Before claiming USB, obtain confirmation that an inserted SD volume was ejected
  in the file manager; leave the card inserted on unpatched stock firmware.
- Do not treat the forced no-card USB selector as physically verified until a
  post-restart no-card boot/access report succeeds. Stock no-card webcam mode
  cannot run this flashing transport.
- Keep each camera's paired full backup immutable. Compare independently read
  data before writing; investigate differences outside settings, never override.
- Preserve all of 0x2fd000..0x2fe000 from the live camera. No whole-chip erase,
  no boot erase, no stock SD-card updater. Never flash the main penguin image here.
- Upload flash source buffers in 512-byte packets and read them back before erase.
  Do not restore the failed 4096-byte upload optimization or remove canaries,
  exact-sector native guards, full read-backs, preimage checks or journaling.
- Stop on any mismatch/uncertain completion. Retain power, journal and allocations;
  no automatic retries, speculative frees, reset, or blind resume. Diagnose first.

## Portability and customization

All documented commands run from this variant folder. build_variant.py recreates
fixed reference builds in ignored analysis/; do not alter those or stock hashes
just to accept another firmware. Convert a complete artwork set and rebuild
from immutable stock, then use the exact shipped app via build_custom_image.py.
The generic artwork updater forbids application changes; initial effect activation
uses the historical exact-build workflow and a fresh hardware rehearsal.

The original fixed-hash installers are evidence of a specific transition, not
universal installers. The new artwork updater is an offline-tested adaptation,
not a hardware-proven replacement. Review its plan and tests before first use.
No hardware checks were performed while packaging this repository folder.

If firmware bytes/helper code/layout differ, stop installation and reverse-engineer
that variant using backups and read-only checks. A model label is not enough.
Changing frame count, controls, effects, code placement or USB policy requires new
native tests, boot-parser checks, a RAM rehearsal and separately approved phases.

When reporting verification, distinguish full flash read-back, offline parser/native
execution, temporary physical RAM feedback, and cold-boot/no-card tests. Do not claim
that an emulated parser proves SDRAM initialization or the complete application boot.

## Public artwork privacy

Children’s faces must stay blurred in source files, prepared resources and all
previews. Do not restore private originals from the owner’s separate project or
ignored local backups into Git. The current public full-image hashes differ from
the historical private installation because of these artwork edits; its native
application is unchanged. When updating an older private installation, use the
retained exact private image as the known preimage, keeping it out of publication.
Do not claim the new public artwork was installed on hardware.
