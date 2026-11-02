# 🚀 دليل الباك إند (Backend) لتشغيل الـ App Clip والـ Live Activity Widget

هذا الدليل مخصص لمطور الباك إند (Backend Developer) الخاص بسيرفر `https://pager.rahapp.com` لتفعيل **Apple App Clip** وبطاقة شاشة القفل والجزيرة التفاعلية (**Live Activity / WidgetKit**) للزبائن فور مسح كود الـ QR.

---

## 1. بيانات التطبيق المعتمدة (Production Identifiers)

| البند | القيمة المعتمدة |
| :--- | :--- |
| **Apple Developer Team ID** | `KRVGAD732T` |
| **App Store App ID** | `6829547522` |
| **معرف التطبيق الكامل (Main App Bundle ID)** | `com.dawrak.app` |
| **معرف الكليب المصغر (App Clip Bundle ID)** | `com.dawrak.app.Clip` |
| **معرف الويدجت (Widget Extension Bundle ID)** | `com.dawrak.app.DawrakWidget` |
| **نطاق السيرفر (Domain)** | `pager.rahapp.com` |

---

## 2. المطلوب على السيرفر: ملف الارتباط مع آبل (AASA File)

لكي يتعرف نظام iOS وكاميرا الآيفون على روابط البيجر ويفتح كرت **App Clip** فوراً عند مسح الباركود، يجب رفع ملف اسمه:
`apple-app-site-association` (بدون أي امتداد .json).

### المسارات المطلوبة على السيرفر (يجب أن يعمل كلاهما):
1. `https://pager.rahapp.com/.well-known/apple-app-site-association`
2. `https://pager.rahapp.com/apple-app-site-association`

### محتوى الملف (JSON):
```json
{
  "appclips": {
    "apps": [
      "KRVGAD732T.com.dawrak.app.Clip"
    ]
  },
  "applinks": {
    "apps": [],
    "details": [
      {
        "appIDs": [
          "KRVGAD732T.com.dawrak.app",
          "KRVGAD732T.com.dawrak.app.Clip"
        ],
        "components": [
          {
            "/": "/pager/*",
            "comment": "Matches any pager token URL"
          },
          {
            "/": "/order/*",
            "comment": "Matches order status URL"
          }
        ]
      }
    ]
  }
}
```

### ⚠️ شروط آبل الصارمة لهذا الملف على السيرفر:
1. **بدون امتداد:** اسم الملف `apple-app-site-association` فقط.
2. **نوع المحتوى (Content-Type):** يجب أن يرجعه خادم Nginx/Apache كـ `application/json`.
3. **بدون إعادة توجيه (No Redirects):** لا يسمح بكود 301 أو 302، يجب أن يرجع كود `200 OK` مباشرة.
4. **شهادة SSL صالحة:** (Let's Encrypt أو مماثل).

---

## 3. المطلوب في صفحات الويب (Smart App Banner Meta Tag)

في صفحة الويب للبيجر (عند فتح العميل لرابط البيجر `https://pager.rahapp.com/pager/{id}` في متصفح سفاري Safari)، يجب إضافة التاج التالي داخل `<head>`:

```html
<meta name="apple-itunes-app" content="app-clip-bundle-id=com.dawrak.app.Clip, app-id=6829547522">
```

> **النتيجة:** بمجرد فتح الصفحة في Safari، سيظهر بنر علوي من نظام iOS يقول: **"فتح بيجر دَورك في App Clip"** بضغطة زر دون تحميل التطبيق!

---

## 4. إرسال إشعارات الجزيرة التفاعلية وشاشة القفل (ActivityKit / APNs)

عند بدء الزبون في متابعة البيجر، يستلم التطبيق رمز `pushToken` خاص بالـ Live Activity ويرسله للسيرفر.

### لإرسال تحديث وتفعيل رنين البيجر على شاشة القفل والجزيرة التفاعلية:
يقوم الباك إند بإرسال طلب **Apple Push Notification Service (APNs)** عبر HTTP/2:

* **Header:**
  * `apns-topic`: `com.dawrak.app.push-type.liveactivity`
  * `apns-push-type`: `liveactivity`
  * `apns-priority`: `10`

* **Payload (JSON):**
```json
{
  "aps": {
    "timestamp": 1714608000,
    "event": "update",
    "content-state": {
      "statusText": "طلبك جاهز للاستلام 🔔",
      "isRinging": true
    },
    "alert": {
      "title": "بيجر دَورك #42",
      "body": "طلبك جاهز الآن، تفضل باستلامه من الكاونتر!"
    },
    "sound": "pager_ring.wav"
  }
}
```

---

## 5. ملاحظة هامة حول مشغل الصوت (Playlist / Now Playing)

* **سبب ظهورها كـ Playlist سابقاً:**  
  إذا تم تشغيل صوت التنبيه عبر مشغل صوتيات دون ضبط جلسة الصوت (AudioSession)، يظن نظام iOS أن التطبيق يشغل بودكاست أو موسيقى ويعرض مشغل الموسيقى (Now Playing) على شاشة القفل.
* **الحل:** تم ضبط تشغيل الصوتيات بحيث تستخدم نغمات التنبيه اللحظية (System Audio Services / Ambient) دون الاستحواذ على مشغل الموسيقى أو إظهار كرت الأغاني.

