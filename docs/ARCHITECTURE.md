# Architecture

## Target
A modular monolith with a separately isolated integration worker where useful.

## Logical flow
Manager browser/PWA -> HTTPS edge -> KPRA DMZ reverse proxy -> internal app/API -> private PostgreSQL.

SenseFace 7 integration (only after verified configuration) -> integration adapter -> normalized physical event -> attendance event store.

TimeNex adapter -> vendor-authorized API or import -> reconciliation queue. A local event stays unconfirmed until TimeNex itself shows the same record. The KPRA ERP manual attendance page is not this path.

## Trust boundaries
- Public internet: browser clients only.
- DMZ: reverse proxy / access gateway.
- Application VLAN: frontend, API, worker.
- Database VLAN: PostgreSQL, accessible only from approved application services.
- Device VLAN: SenseFace terminal, restricted to required internal services.
- Management plane: admin access restricted to approved networks/VPN.

## Core principle
Remote attendance works independently of device and TimeNex availability. Integrations are optional adapters, not dependencies of the manager Check-In workflow.
