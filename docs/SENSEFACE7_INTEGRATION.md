# SenseFace 7 Series Integration

Status: feasibility recorded. No protocol listener is implemented. The production terminal must stay on its current server.

## Verified from vendor materials

Sources reviewed on 3 November 2026:

- ZKTeco SenseFace 7 Series product page: https://www.zkteco.com/en/HybridBiometric4ZM/SenseFace_7_Series
- SenseFace 7A product description: https://www.zkteco.sa/product-details/senseface-7a
- SenseFace 7 Series user manual, English, v1.0 (20240325): https://zktecocolombia.com/wp-content/uploads/2024/11/ZK_SenseFace-7-Series_UM_EN-v1.0_20240325.pdf
- SenseFace 7A instruction PDF: https://zkteco-online.com/files/instruction/senseface-7a.pdf

Verified statements:

- The series supports fingerprint, face, card, and password on the terminal. Supported software named by ZKTeco includes ZKBio CVSecurity (AC Push), ZKBio Time (TA Push), and ZKBio Zlink (BEST protocol). This application is none of those products.
- Standard functions listed by ZKTeco include ADMS and AC Push / TA Push protocol switching.
- Device type setting: the manual says the terminal can be set as a time-attendance terminal (T&A PUSH) or an access-control terminal (A&C PUSH).
- Cloud Server Setting connects the device to an ADMS server. With domain name disabled, Server Address is the ADMS server IP and Server Port is the port used by that ADMS server. Direction is device to server.
- TCP COMM Port is a separate setting. The manual says its default value is 4370 and that it can be changed. That is the device's own TCP communication port, not the ADMS server port.
- Connecting to ZKBio Time: set Cloud Server Setting to the BioTime server. The manual says the default server port is 8081.
- Connecting to ZKBio CVSecurity / CVAccess: set the device cloud server port to the ADMS service port configured in that software's Communication Monitor. The manual does not give a single universal ADMS port.
- USB disk transfer of user and attendance data is documented, FAT32 only. The manual does not publish a stable file layout we can claim to parse.
- The manual's cloud-server chapter does not publish the ADMS/PUSH HTTP body, headers, or acknowledgement schema.

## Observed on the supplied terminal

These are observations from the photographs already described in the project brief. They are not a change request.

- Device: SenseFace 7A
- Serial: VDE2244500097
- MAC: 00:17:61:10:ad:46
- Platform: ZAM70_TFT
- Fingerprint algorithm: ZKFinger VX13.0
- Face algorithm: ZKFace VX4.0
- Server mode: ADMS
- Server address: 173.208.167.249
- Server port: 80
- Domain name: disabled
- Proxy: disabled
- TimeNex device list, read on 3 November 2026, titles this serial "KPRA Head office". The ADMS address on the terminal is the existing TimeNex path. This application does not replace it.

The exact firmware build, the T&A versus A&C device-type setting, and whether 173.208.167.249 is operated by TimeNex are not verified. Port 80 matches a device-to-server ADMS path. It is not port 4370 and it is not the ZKBio Time default of 8081.

## What this application does

- Keeps a device registry row for the observed terminal with status `NOT_CONNECTED`.
- Does not open an ADMS, TA Push, AC Push, or TCP 4370 socket.
- Does not change, and does not instruct an operator to change, the terminal's server address or port.
- Does not store fingerprint images, face images, or biometric templates.
- Accepts an administrator-prepared CSV, using this application's own template, as a manual fallback. That import is not a ZKTeco USB parser and is not a live device feed.
- Shows integration status as listener disabled. It never shows the device as online and never shows TimeNex as synced.

`SENSEFACE_ADMS_ENABLED=true` does not start a listener. The worker marks the integration misconfigured and sends no traffic, because no verified payload implementation is included.

## Safe checklist (do not perform on the live terminal without a change window)

1. Photograph the current COMM, Cloud Server, Device Type, and firmware screens again and file them with the change record.
2. Confirm who operates 173.208.167.249:80 and that attendance is still arriving in the existing system.
3. Get the exact firmware build and the current device type (T&A PUSH or A&C PUSH) from the device menus.
4. If a future project implements a receiver, lab-test it with a non-production terminal and the vendor's documented software or a written protocol specification.
5. Only then schedule a change window with a rollback that restores 173.208.167.249 port 80.

## Unknowns

- Firmware build number.
- Whether the installed mode is T&A PUSH, A&C PUSH, or BEST.
- Who operates the current ADMS endpoint.
- Whether that hop is encrypted.
- The push payload schema, employee PIN field, acknowledgement, retry, and duplicate behavior for this firmware.
- The on-disk layout of a USB attendance download.
