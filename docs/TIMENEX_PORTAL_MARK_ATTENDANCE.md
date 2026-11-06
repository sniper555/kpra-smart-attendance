# TimeNex Mark Attendance request

Observed 5 Nov 2026 by signing in to https://www.timenex.net:8443/ and opening the existing Mark Attendance screens. Save was not clicked. No attendance was submitted. No response body and no TimeNex reference were captured.

The portal password stays in `.env` only. This document does not repeat it, and it does not repeat the session token.

## Sign-in

`POST /loginaction`

`Content-Type: application/x-www-form-urlencoded`

| Field | Meaning |
| --- | --- |
| `_token` | Value from the login page hidden field |
| `email` | Portal username |
| `password` | Portal password |

A successful sign-in continues to `/dashboard`. Later forms include a new `_token`. The session is a cookie. The cookie name was not recorded.

## Find the employee

`GET /marks_attendance` shows one search box.

`POST /employee/search`

| Field | Meaning |
| --- | --- |
| `_token` | Current page token |
| `search` | Name, ID, or CNIC, as the placeholder states |

Searching the TimeNex ID `22091` returned a result page. That page linked to `GET /marks_attendance/mark_attendance/22091`. The mark form's hidden `id` was `22091`. For this lookup, the TimeNex ID was the key. A CNIC and a file number were not shown to be the same key.

## Mark one day

`POST /marks_attendance/markattendance`

`Content-Type: application/x-www-form-urlencoded`

| Field | Required on the form | Meaning |
| --- | --- | --- |
| `_token` | Yes | Current page token |
| `id` | Yes, hidden | TimeNex employee ID |
| `attendance_date` | Yes, `type="date"` | Date, `YYYY-MM-DD` |
| `time_in` | Yes, `type="time"` `step="1"` | Time in. The control is a 24-hour clock with seconds |
| `time_out` | No, `type="time"` `step="1"` | Time out. A blank out time is still posted as `time_out=` with an empty value. The daily report Out column is the later punch on that date, so a device punch remains in Out when only an in time is saved |
| `ack` | Checkbox, not marked required in the HTML | Present only when checked. The label says the save replaces the previous attendance for that date and cannot be reverted |

There is no field for check-in versus check-out, and no field for `REMOTE_WEB` or `REMOTE_MOBILE`. One submit writes the day's time in and time out together.

The page heading is Mark Attendance for the selected person. The warning on the form says replacement is permanent.

## What was not observed

A live Save was not sent during the form check. The client treats HTTP success without a danger alert as acceptance and stores the reference `portal:{employee id}:{date}`. The response body is kept on the local row when the portal rejects the day.

The local check-out of a verified mapped employee calls this form after the local row is saved. Check-in does not call it. A manual sync can send a day that has a check-in and no check-out. If that day is not today, or TimeNex already shows a time for it, the sync waits until the user confirms “Overwrite TimeNex day with these times”. An administrator mark stays local unless the verified-link checkbox is ticked. That tick uses the same form and replaces the day.

## What this application will not do

Check-in does not call this form. Check-out does, after the local row is saved, and only for a verified TimeNex ID. That save replaces the official day in TimeNex, including a time that came from the SenseFace terminal.

This application will not send an ADMS attendance log that uses the SenseFace serial `VDE2244500097` and a face or fingerprint verify mode. The terminal's ADMS address stays unchanged.
