# 💳 دليل تكامل ماي فاتورة (MyFatoorah Embedded Payment v3) — Trings

> **المشروع:** `/var/www/pager` — FastAPI
> **الوضع:** Embedded v3 (Unified Session) بنمط `COMPLETE_PAYMENT`، بدون تحويل خارجي، مع حفظ البطاقة (Token).

---

## 1. طريقة العمل

1. الصفحة تنشئ **intent** على السيرفر (`POST /api/billing/checkout/intent`) — المبلغ يُحسب على السيرفر فقط.
2. `POST /api/billing/checkout/session` → السيرفر يستدعي `POST /v3/sessions` مع:
   - `PaymentMode: COMPLETE_PAYMENT`، `Order.Amount/Currency`، `Order.ExternalIdentifier = intent_id`
   - `Customer.Reference = rest_{id}`، `SaveCardOptions.SaveToken` حسب خيار "حفظ البطاقة"
   - يحفظ `SessionId` و `EncryptionKey` (مشفّر) على الـ intent، ويرجع للمتصفح `SessionId` فقط.
3. المتصفح: `myfatoorah.init({ sessionId, containerId: "mf-form", callback, shouldHandlePaymentUrl: true, eventListener, subscribedEvents, settings })`
   — زر الدفع خاص بنا (`useCustomButton: true`) ويستدعي `myfatoorah.submitCardPayment()`. ماي فاتورة تعرض صفحة 3DS بنفسها.
4. `callback(response)` → `POST /api/payment/myfatoorah/process` بـ `{ intent_id, session_id, payment_data, redirection_url }`.
5. السيرفر: يتحقق أن `session_id` يخص الـ intent، يفك `paymentData` بالـ `EncryptionKey` لاستخراج `PaymentId`،
   ثم **يتحقق دائماً** عبر `GET /v3/payments/{id}` (الحالة، المبلغ بعملة العرض، `ExternalIdentifier`) قبل التسوية.
   أي اختلاف في المبلغ أو الـ intent = لا تسوية.

الكود: `app/myfatoorah_service.py` · `app/payments.py` · `app/static/js/mf-embedded.js` (مشترك) ·
`subscription-checkout.js` / `billing-checkout-page.js` / `pay-invoice.js`.

---

## 2. السكربت والروابط

| البيئة | `session.js` | API |
|---|---|---|
| test | `https://demo.myfatoorah.com/sessions/v1/session.js` | `https://apitest.myfatoorah.com` |
| live (BH/KW/OM/JO) | `https://portal.myfatoorah.com/sessions/v1/session.js` | `https://api.myfatoorah.com` |

تُختار حسب `MYFATOORAH_ENV` (override: `MYFATOORAH_TEST_SESSION_SCRIPT_URL` / `MYFATOORAH_SESSION_SCRIPT_URL`).
الكائن العام في المتصفح هو `window.myfatoorah` (أحرف صغيرة).

---

## 3. نقاط النهاية

| المسار | الوظيفة |
|---|---|
| `POST /api/billing/checkout/intent` | إنشاء/إعادة استخدام intent + فاتورة غير مدفوعة |
| `POST /api/billing/checkout/session` | جلسة v3 للـ intent (`save_card`) |
| `POST /api/payment/myfatoorah/process` | تسوية نتيجة الـ callback (يوجد alias: `/api/billing/checkout/process`) |
| `POST /api/billing/pay-link/session` · `/process` | نفس التدفق لرابط دفع الفاتورة (ضيف) |
| `POST /api/billing/checkout/charge-card` | خصم بطاقة محفوظة: `POST /v3/payments` بـ `SourceOfFund.Token` |
| `POST /api/payment/myfatoorah/webhook` | Webhook (HMAC-SHA256) → نفس التسوية |
| `GET /payment/myfatoorah/return?paymentId=&intent=` | صفحة النتيجة (تسوية idempotent) |

---

## 4. واجهة الدفع

- صندوق البطاقة `.mf-box` يبقى ظاهراً دائماً: skeleton أثناء التحميل حتى `VIEW_READY`،
  وعند أي فشل تظهر الرسالة تحته مع زر **حاول مرة أخرى** (جلسة جديدة لنفس الـ intent).
- في وضع test تظهر رسالة السيرفر/البوابة الفعلية بجانب الرسالة العامة، وكل خطوة تُسجَّل في `console`.

---

## 5. بطاقات الاختبار (demo)

| النتيجة | البطاقة | الانتهاء | CVV |
|---|---|---|---|
| ناجح (3DS → اختر `(Y)`) | `5123450000000008` | 01/39 | 100 |
| مرفوض بسبب 3DS (اختر `(N)`) | `5123450000000008` | 01/39 | 100 |
| مرفوض (`BLOCKED`) | `345678901234564` | 04/37 | 1000 |

الاسم: أي اسم من جزأين مثل `test test`.
