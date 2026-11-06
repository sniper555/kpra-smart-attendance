# KPRA ERP attendance investigation

Status: historical investigation only. The ERP manual page is not the application architecture. TimeNex is the attendance destination. Save was not clicked on 4 Nov 2026, and no attendance was posted to the ERP.

Page: https://erp.kpra.gov.pk/attendance/manual

The address `https://erp.kpra.gov.pk/attendace` is not the page that was open. The working page is `/attendance/manual`.

## What the page does

The signed-in page is titled Mark Attendance. It has an employee list, a date, an in time, an out time, Save, and Cancel.

The page states: “A day saved here is shown as Present* on reports. A fingerprint or device punch stays Present.”

Save is a normal form post back to `https://erp.kpra.gov.pk/attendance/manual`. The fields are:

| Field | Meaning seen on the page |
| --- | --- |
| `empID` | The selected option value |
| `att_date` | The attendance date |
| `punch_in` | In time |
| `punch_out` | Out time |
| `save_mark` | `1` when Save is clicked |

The browser script checks that an employee, a date, and at least one time are present, and that the out time is not earlier than the in time. If those checks pass, the script does not cancel the post. The post is the ERP’s own signed-in form. It is not a separate external API, and this application does not send it.

Choosing an employee calls `manual_save_ajax?empID=&date=`. The script expects JSON with `in`, `out`, `exists`, `status`, `status_label`, `date_label`, `in_label`, and `out_label`. When a row already exists, the page says “This date already has {status}. Save replaces it.” A later request to that address from the same browser returned the ERP sign-in HTML, so that read is session-bound and was not treated as an open API.

The date control on the open page had a maximum of 2026-11-03.

## Employee identifiers

The list had 106 options. The option value is an internal key. On the sampled options the values were small numbers such as 21, 116, 50, and 65. Seventy-five labels also show a separate staff number after the name. In all 106 options, that displayed number was not the same as the option value. Ali and employee 20043 were not in this list.

This application therefore does not copy a TimeNex employee ID into `empID`. Those identifiers were not shown to be the same value.

## What was not found

- No ERP source code is in this repository.
- The manual page does not mention TimeNex.
- No token, webhook, or external attendance API was present on this page.
- The page itself does not show the TimeNex result. The owner's later operational report, recorded below, is that a day saved on this form appears in TimeNex.

## SenseFace and TimeNex

The production SenseFace 7A remains on ADMS at 173.208.167.249 port 80. Those settings were not changed. Vendor material still lists ADMS, AC Push, and TA Push as separate device modes, and ZKBio Time and ZKBio CVSecurity as separate products. This application does not speak those protocols and does not impersonate the terminal. The earlier record is in `docs/SENSEFACE7_INTEGRATION.md` and `docs/TIMENEX_FEASIBILITY.md`.

The owner's operational report is that a person who saves this form in the ERP later sees the same day in TimeNex. This application has not reproduced that save, and it does not treat that report as permission to post the form.

## What is still required before this application can submit

An ERP administrator or the ERP vendor must provide all of the following. A browser session cookie is not a substitute.

1. An application credential for the attendance write, separate from an interactive sign-in.
2. The write operation: method, URL, and body. The page post is `empID`, `att_date`, `punch_in`, `punch_out`, and `save_mark=1` to `/attendance/manual`. That is the browser form, not a published application contract.
3. The rule for `empID`. On the inspected list it is an internal key and it is not the staff number shown beside the name, and it is not the TimeNex ID in the supplied sheet. Each person needs that internal key recorded here.
4. The response field that means the ERP accepted the row, and the reference to store.
5. An authorized read that can show the same employee, date, and times in TimeNex after the ERP accepts the row. ERP acceptance alone must not be displayed as TimeNex confirmation.
6. Confirmation of the overwrite rule. The page says a second save replaces the existing day.

Until those items exist, check-in is stored here, the ERP page is not called, and TimeNex stays unconfirmed.
