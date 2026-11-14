# Stick on WhatsApp: setup checklist

Stick keeps working on Telegram throughout. WhatsApp is a second channel that switches on once the settings below exist.
**Time-box this.** If it isn't working after about two hours, stop and carry on with Telegram. The competition doesn't require WhatsApp.

## What Meta allows (checked November 2026)
- The WhatsApp Cloud API works **without business verification**, capped at 250 unique people per 24 hours for messages *Stick starts*. Replies to people who message Stick first aren't capped.
- Your number **must not already be registered on WhatsApp** (personal or Business app). If it is, delete that WhatsApp account first. Its chat history is lost.
- Adding and verifying a number **does not register it for the API**. Step 4 does.
- Reminders sent days after someone's last message need an **approved template** (step 9). Replies inside 24 hours of their message are free-form.
- Meta charges for template messages. Check the current rates for Nigeria in WhatsApp Manager before you rely on reminders.

## Steps
1. **Meta Business account:** business.facebook.com, create one if you don't have it.
2. **Developer app:** developers.facebook.com, My Apps, Create App, type **Business**, add the **WhatsApp** product, and link your Business account.
3. **Add your number:** WhatsApp, API Setup, **Add phone number**. Enter your dedicated number, choose a display name (for example "Stick Study"), and verify with the SMS or call code.
4. **Register it for the API:** copy the number's **Phone Number ID** (not the phone number) from API Setup. Put these in your local `.env`: `WHATSAPP_TOKEN` (temporary token for now) and `WHATSAPP_PHONE_ID`. Then run:
   ```
   python -m app.wa_setup register 123456
   python -m app.wa_setup status
   ```
   Choose your own 6-digit PIN and keep it. `status` should say **CONNECTED**.
5. **Permanent token** (the API Setup token expires in about a day): Business Settings, Users, **System users**, add one, assign your app and WhatsApp account with full control, then **Generate token** with `whatsapp_business_messaging` and `whatsapp_business_management`. Copy it once and store it safely.
6. **Render settings** (Environment tab of the web service, then save):
   - `WHATSAPP_TOKEN`: the permanent token
   - `WHATSAPP_PHONE_ID`: the Phone Number ID
   - `WHATSAPP_VERIFY_TOKEN`: any string you make up (letters, digits, `_`, `-`)
   - `WHATSAPP_APP_SECRET`: App settings, Basic, **App secret**
   - `PRIVACY_CONTACT`: an email or WhatsApp link people can use to ask for their data to be deleted
7. **Webhook:** in the developer dashboard, WhatsApp, Configuration, Callback URL `https://<your-service>.onrender.com/whatsapp/webhook`, Verify token = your `WHATSAPP_VERIFY_TOKEN`, then **Verify and save**. Under Webhook fields, subscribe to **messages**. Then run `python -m app.wa_setup subscribe <WABA_ID>` (your WhatsApp Business Account ID is on the API Setup page).
8. **Go Live:** App settings, Basic: add **Privacy Policy URL** `https://<your-service>.onrender.com/privacy`, then switch the app from Development to **Live**.
9. **Reminder template:** WhatsApp Manager, Message templates, Create. Name `study_review_due`, category **Utility**, language English, body:
   `Hi {{1}}, {{2}} topic(s) are due for review today. Reply QUIZ to start.`
   Add sample values (Ada, 2). Wait for approval.
10. **Test:** message your number from another phone with `study`. Open `/diag?key=<TICK_KEY>` and check the `whatsapp` block says CONNECTED.

## Things that commonly go wrong
| Symptom | Likely cause |
|---|---|
| Number won't register | It's still on a WhatsApp account, or you skipped step 4 |
| Verify and save fails | `WHATSAPP_VERIFY_TOKEN` differs between Render and Meta |
| Messages arrive but no replies | `WHATSAPP_TOKEN` expired (temporary token) or wrong Phone Number ID |
| No messages arrive at all | Skipped `subscribe`, the "messages" field, or the app isn't Live |
| 403 in Render logs on `/whatsapp/webhook` | `WHATSAPP_APP_SECRET` is wrong |
| Reminders never arrive after a day | The template isn't approved yet, or its name differs from `WHATSAPP_REMINDER_TEMPLATE` |
