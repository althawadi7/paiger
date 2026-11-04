# 💳 دليل تكامل بوابة ماي فاتورة (MyFatoorah) للدفع المباشر وحفظ البطاقات (Paiger Web & Backend)

> **المشروع:** بيجر دَورك (`/var/www/pager`) — خادم FastAPI وموقع الويب  
> **الهدف:** توفير دفع إلكتروني مباشر داخل الموقع (In-App / Embedded) بدون تحويل خارجي (No Outside URL Redirect) مع دعم حفظ البطاقات (Save Card / Tokenization) وApple Pay وبنفت والبطاقات البنكية.

---

## 1. فكرة العمل (How It Works Without Redirect)

بدلاً من تحويل العميل إلى رابط خارجي (`PaymentURL` التابع لماي فاتورة)، يتم استخدام **Embedded Payment Session (`session.js`)**:
1. المتصفح أو التطبيق يطلب جلسة دفع مشفرة من السيرفر عبر: `POST /api/payment/myfatoorah/session`.
2. السيرفر يتصل بـ MyFatoorah API (`/v3/sessions`) ويرجع `SessionId`.
3. مكتبة ماي فاتورة في الموقع تعرض حقول البطاقة / Apple Pay مباشرة داخل الصفحة بداخل حاوية مخصصة (`<div id="mf-form"></div>`).
4. عند الضغط على زر "دفع":
   - تقوم الدالة `myFatoorah.submit()` بتشفير البطاقة وإرجاع Session موثقة.
   - يرسل الموقع الطلب للباك إند: `POST /api/payment/myfatoorah/process`.
   - يقوم الباك إند بتنفيذ الخصم فوراً عبر `/v3/payments`.
   - في حال اختيار العميل "حفظ البطاقة" (`save_card: true`)، يتم حفظ توكن البطاقة لاستخدامه لاحقاً بنقرة واحدة!

---

## 2. نقاط النهاية المتوفرة في السيرفر (API Endpoints)

| المسار (Path) | الطريقة (Method) | الوظيفة |
|---|---|---|
| `/api/payment/myfatoorah/config` | `GET` | إرجاع إعدادات الدفع وعنوان سكريبت `session.js` والعملة |
| `/api/payment/myfatoorah/session` | `POST` | بدء جلسة دفع مشفرة والحصول على `SessionId` لعرض الحقول |
| `/api/payment/myfatoorah/process` | `POST` | تنفيذ الدفع النهائي وربط العملية وتفعيل ميزة حفظ البطاقة |
| `/api/payment/myfatoorah/charge-token` | `POST` | الخصم المباشر من بطاقة محفوظة مسبقاً (Token) دون إدخال البيانات |
| `/api/payment/myfatoorah/webhook` | `POST` | استقبال إشعارات الدفع والتحقق من التوقيع الرقمي (HMAC-SHA256) |
| `/payment/myfatoorah/return` | `GET/POST` | صفحة التحقق عند العودة من خطوة التحقق البنكي (3DS / OTP) |
| `/checkout/{order_id}` | `GET` | صفحة ويب تجريبية متكاملة للدفع المباشر وحفظ البطاقات |

---

## 3. مثال الاندماج في الواجهة الأمامية للموقع (Frontend Integration)

### أ) تضمين مكتبة ماي فاتورة:
```html
<script src="https://api.myfatoorah.com/payment/v1/session.js"></script>
```

### ب) إضافة حاوية الدفع:
```html
<div id="mf-form"></div>
<button id="btn-pay" onclick="payNow()">تأكيد ودفع</button>
```

### ج) كود الجافاسكريبت (JavaScript):
```javascript
// 1. جلب SessionId من السيرفر
const res = await fetch('/api/payment/myfatoorah/session?amount=5.0', { method: 'POST' });
const { session } = await res.json();

// 2. تهيئة النموذج المباشر
myFatoorah.init({
    countryCode: "BHR",
    sessionId: session.SessionId,
    cardViewId: "mf-form",
    style: {
        direction: "rtl",
        cardHeight: 180,
        input: {
            color: "#ffffff",
            fontSize: "15px",
            fontFamily: "'Readex Pro', sans-serif"
        }
    }
});

// 3. تنفيذ الدفع عند الضغط
function payNow() {
    myFatoorah.submit().then(async (response) => {
        const payRes = await fetch('/api/payment/myfatoorah/process', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                order_id: "ORDER_123",
                amount: 5.0,
                session_id: response.sessionId,
                save_card: true
            })
        });
        const result = await payRes.json();
        if (result.ok) {
            window.location.href = result.redirect_url || '/payment/success';
        }
    }).catch(err => {
        alert('خطأ في بيانات البطاقة');
    });
}
```

---

## 4. الدفع بنقرة واحدة بالبطاقة المحفوظة (One-Click Token Payment)

عندما يكون لدى العميل بطاقة محفوظة مسبقاً (`Token`)، لا داعي لطلب رقم البطاقة نهائياً:
```javascript
await fetch('/api/payment/myfatoorah/charge-token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        order_id: "ORDER_123",
        amount: 5.0,
        token: "TKN_xxxx_xxxx",
        security_code: "123" // اختياري حسب إعدادات الحساب
    })
});
```

---

## 5. تجربة الصفحة المباشرة الآن

يمكنك فتح وتجربة واجهة الدفع المباشرة مباشرة عبر الرابط:
`https://pager.rahapp.com/checkout/TEST_1001?amount=5.0`
