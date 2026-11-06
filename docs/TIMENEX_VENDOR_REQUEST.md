# Request for the TimeNex vendor

Send this to the operator of the KPRA tenant at https://www.timenex.net:8443/. Nothing in this application can submit attendance until these answers arrive in writing.

KPRA Smart Attendance records a manager check-in and check-out in its own database. Those records must then appear in the same employee's TimeNex attendance report. The signed-in TimeNex menus contain reports and export (CSV, Excel, PDF) and a one-person Mark Attendance screen. They do not contain an API token, an import template, or a file upload for attendance. The KPRA ERP manual page is not the integration path. The SenseFace terminal and its ADMS address must stay unchanged.

Please provide all of the following:

1. Product name and version of this tenant, and the written integration document for that version.
2. An application credential that is not a person's TimeNex password: who issues it, how it is rotated, and which network may use it.
3. The attendance write: method, URL, and body. It must keep a remote browser event distinct from a terminal punch. This application sends `REMOTE_WEB` or `REMOTE_MOBILE`. It will not imitate a SenseFace event.
4. The employee key that write expects. A sheet ID, an ERP option value, a CNIC, and a file number have not been shown to be the same key.
5. Whether check-in and check-out are one row or two events, how a missing check-out stays missing, and how a duplicate for the same employee and date is rejected.
6. The response field that means TimeNex accepted the row, and the reference this application must store.
7. An authorized read, with the same credential or a read-only credential, that returns the employee, date, time in, and time out. Confirmation uses that read.
8. The timezone of stored times. This application stores UTC and displays Asia/Karachi.
9. Whether TimeNex accepts a remote browser event at all. If it accepts only terminal punches, say so.
10. A non-production tenant, or a written test window, so the first submission is not a live payroll record.

Until that contract exists, a check-in in KPRA Smart Attendance stays in this application. It is not shown as confirmed in TimeNex.
