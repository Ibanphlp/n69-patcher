# N69 A9 evidence by stage

The owner reports a return to iOS 15. No trace from that attempt identifies execution of donor kernel 20H392. SevenUP documents a byte-for-byte kernel comparison and a working reference phone for its D10 A10 target. The SE still needs its own reference. [SevenUP validation](SevenUP-main/docs/VALIDATION.md) · [Historical comparison](AUDIT_N69_SEVENUP.md)

| Stage | Implementation in N69 | Host or structural evidence | Evidence on this SE and open question |
|---|---|---|---|
| APFS | SystemB at s8, final Recovery role 0x04. Existing iOS 15 Data (0x40) and systembag are shared. Only SystemB is created. | Simulated fresh and existing layouts, old unroled s9, conflicting roles and invalid checksums. | Actual donor mount and effects on shared Data remain unknown. |
| Rootfs and Cryptex | Separate stream, Cryptex and patch stages. The shell wrapper requests core for all three kernel modes. | The core dry run selects one stream, two overlays (ticket and N69 model files) and two finalizers (finish and roles). The last saved host selection lists individual resources. | Contents of the currently installed filesystem require a device readback. |
| Boot images and handoff | The manifest records kernel mode and selected patches. Capture hashes the local images and timestamps the host handoff. | Offline image checks and simulated collection. | The reported logo/backlight and subsequent iOS 15 response leave the cause and exact reset time unknown. |
| PMU | D2255 selected in safe-minimal and full. | 68 address mappings in the archived reference transplant. | ABI compatibility and a PMU probe remain unknown. |
| Display | Chestnut power and Pinot LCD selected in full. | Five Pinot mappings. Chestnut has a separate path outside the three archived trials. | Panel power and donor display output each need an observation. |
| Touch | SPI driver and SPIN69 personality selected in full. | One SPI mapping. A separate typed-field comparison checks the SPIN69 personality. | Provider attachment and a received touch event remain unobserved. |
| SEP | Legacy shim selected in safe-minimal and full. | Component metadata and host image checks. | A SEP service response remains unobserved. |
| Other hardware | Audio, charger, Wi-Fi and haptic kernel patches disabled in these presets. | Host mode-selection tests. | Device behavior remains unknown. |
| Reports and regression reference | The capture groups the trial question, mode, hashes, timestamps and available device observations under one attempt ID. | Fixtures cover incomplete, standalone and matched captures, old panics and a simulated live donor OS response. | No attributable donor-kernel trace or successful SE reference yet. Stored reports retain their original time, build and unassigned attribution. |
| Distribution | Both archive entry points package the offline audit and its required Python modules. | A clean extraction imports the actual SevenUP engine. Private-state fixtures, missing sources and symlink substitution are tested. | This package is for host-side inspection. |

The archived structural results identify their input and output kernel hashes. The numbers count address mappings. [Structural report](research/n69-compatibility-report.json) · [Component metadata](research/n69-reference-metadata.json)
