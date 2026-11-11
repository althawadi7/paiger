import os
import pymysql
from dotenv import load_dotenv

load_dotenv('/var/www/pager/.env')

conn = pymysql.connect(
    host=os.getenv('DB_HOST', '127.0.0.1'),
    port=int(os.getenv('DB_PORT', 3306)),
    user=os.getenv('DB_USER', 'pager_user'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME', 'pager_db'),
    cursorclass=pymysql.cursors.DictCursor
)

pages = [
    {
        'slug': 'privacy-policy',
        'title': 'سياسة الخصوصية',
        'title_en': 'Privacy Policy',
        'meta_description': 'سياسة الخصوصية وحماية بيانات المستخدمين في منصة Trings Trings SaaS',
        'sort_order': 1,
        'content': '''# سياسة الخصوصية لمنصة Trings (Trings SaaS)
**تاريخ آخر تحديث:** 1 نوفمبر 2026

تلتزم منصة **بيجر (Trings SaaS)** وتطبيقها وخدمة **Apple App Clip** التابعة لها بأعلى معايير حماية الخصوصية وأمن البيانات، والامتثال التام لإرشادات متجر تطبيقات أبل (**Apple App Store Review Guidelines - Section 5.1**). نوضح في هذه الوثيقة الشفافة ماهية البيانات التي نتعامل معها وكيفية حمايتها.

---

### 1. البيانات التي لا نجمعها إطلاقاً (عدم طلب أي بيانات شخصية للعميل)
* **لا نطلب إنشاء حساب للعملاء:** لا يحتاج العميل (زائر المطعم أو المقهى) إلى التسجيل، أو إدخال اسمه، أو بريده الإلكتروني، أو كلمة مرور، أو رقم هاتفه.
* **لا نجمع بيانات الموقع الجغرافي (GPS):** لا نطلب ولا نتتبع موقعك الجغرافي الدقيق أو التقريبي.
* **لا نصل إلى جهات الاتصال أو الصور الشخصية:** لا يصل تطبيقنا أو الكليب الخاص بنا إلى سجل الأسماء أو معرض الصور بجهازك.
* **لا نقوم بالتعقب الإعلاني (No Cross-App Tracking):** لا نستخدم معرّفات التتبع الإعلاني مثل IDFA أو AdID، ولا نشارك أي بيانات مع شبكات إعلانية أو وسطاء بيانات (Data Brokers).

---

### 2. الصلاحيات والبيانات التقنية التي نستخدمها وكيفية توظيفها
عند استخدام تطبيق بيجر أو الـ App Clip، نتعامل فقط مع الحد الأدنى اللازم لتشغيل الخدمة:
1. **صلاحية الكاميرا (Camera Permission):**
   * تُستخدم الكاميرا حصرياً وفقط لمسح رمز الاستجابة السريع (QR Code) الموجود على شاشة الكاشير أو ستاند الطاولة.
   * معالجة الصورة تتم محلياً 100% داخل جهازك (On-Device). لا يتم التقاط صور فوتوغرافية، ولا يتم تسجيل فيديو، ولا يتم إرسال أي صورة إلى خوادمنا.
2. **رمز الطلب المؤقت (Ephemeral Order Token):**
   * عند مسح الكود، يتم توليد رمز عشوائي ومؤقت (مثلاً: `11d4ba728e`) يربط جهازك برقم طلبك داخل المقهى (مثلاً: الطلب رقم #42).
   * هذا الرمز لا يحتوي على أي هوية شخصية، وتنتهي صلاحيته تلقائياً بمجرد استلام طلبك ومغادرة المكان.
3. **تقنية الأنشطة الحية وشاشة القفل (Apple Live Activities & APNs Token):**
   * عند تفعيل بطاقة الطلب في الجزيرة التفاعلية (Dynamic Island) أو شاشة القفل، يولد نظام iOS رمزاً مؤقتاً مشفراً (ActivityKit Push Token).
   * يُستخدم هذا الرمز حصرياً لإرسال إشعار لحظي لجهازك عندما يضغط الكاشير على زر "رنين / جاهز للاستلام"، ويتم حذفه فورياً عند اكتمال الطلب.

---

### 3. بيانات مدراء المطاعم والكاشير (Merchants)
بالنسبة للمطاعم وملاك الفروع المشتركين في الخدمة:
* نجمع الاسم التجاري للمطعم، المعرف اللاتيني (Slug)، البريد الإلكتروني المعتمد للإدارة، ورقم الهاتف للتواصل والدعم الفني.
* يتم تشفير كلمات المرور باستخدام خوارزميات التشفير المتقدمة (Bcrypt Salted Hashing)، ولا يمكن لأي موظف الاطلاع عليها.

---

### 4. أمن البيانات والاستضافة
* يتم تشفير جميع الاتصالات بين جهازك وخوادمنا باستخدام بروتوكولات الأمان القياسية **TLS 1.3 / HTTPS** وقنوات **WSS (Secure WebSockets)**.
* تُخزن البيانات داخل مراكز بيانات سحابية معزولة ومحمية بجدران نارية متطورة ضد الهجمات الإلكترونية.

---

### 5. حقوق المستخدم والاتصال بنا
لك الحق الكامل في أي وقت في الاستفسار عن بياناتك أو طلب حذف أي سجلات مرتبطة بفرعك التجاري.
لأية استفسارات تتعلق بالخصوصية، يمكنك التواصل مع فريق حماية البيانات عبر:
* **البريد الإلكتروني للخصوصية:** privacy@rahapp.com
* **الدعم الفني العام:** support@rahapp.com
* **الموقع الرسمي:** https://pager.rahapp.com''',

        'content_en': '''# Privacy Policy for Trings SaaS & App Clip
**Last Updated:** November 1, 2026

**Trings SaaS** ("we", "our", or "the Service"), including our iOS Application and **Apple App Clip**, is dedicated to safeguarding your privacy and complying fully with **Apple App Store Review Guidelines (Section 5.1 - Data Collection and Storage)**. This Privacy Policy details our transparent and privacy-first data practices.

---

### 1. Data We Do NOT Collect
* **No End-User Registration:** Diners and guests scanning a table QR code or cashier screen are NOT required to register, provide their real name, phone number, email address, or password.
* **No Geolocation Tracking:** We never request, collect, or store your GPS or network location.
* **No Access to Contacts or Media:** Our app and App Clip never access your address book, media library, or local documents.
* **No Advertising Trackers:** We do not track users across other companies' apps or websites (No IDFA tracking), nor do we sell or disclose data to third-party data brokers.

---

### 2. Device Permissions & Technical Usage
1. **Camera Access:**
   * Used strictly and solely to scan optical QR codes displayed at the counter or printed on table stands.
   * Frame analysis takes place strictly on-device. No photographs or video streams are saved or transmitted to our servers.
2. **Ephemeral Order Tokens:**
   * When you scan an order QR code, a randomized alphanumeric token (e.g., `11d4ba728e`) is associated with your queue number.
   * This token carries no personal identity and expires automatically upon order fulfillment.
3. **Apple Live Activities & Dynamic Island:**
   * To display your live queue progress on the Lock Screen and Dynamic Island, iOS generates an ActivityKit Push Token.
   * This token is used exclusively to ping your device when the barista or cashier rings your order. It is invalidated and removed once the order is complete.

---

### 3. Merchant & Restaurant Account Information
For registered cafes and food & beverage establishments:
* We collect business entity names, manager contact emails, and business phone numbers for administrative billing and operational purposes.
* All passwords are encrypted with industry-standard salted Bcrypt hashing algorithms.

---

### 4. Data Security & Encryption
* All network transmissions are strictly enforced over **TLS 1.3 / HTTPS** and **WSS (Encrypted WebSockets)**.
* Server systems and MariaDB databases are hosted in ISO-certified European data centers with strict role-based access control.

---

### 5. Contact & Privacy Inquiries
For any questions regarding this Privacy Policy or data protection, please contact:
* **Privacy Officer:** privacy@rahapp.com
* **Customer Support:** support@rahapp.com
* **Platform URL:** https://pager.rahapp.com'''
    },
    {
        'slug': 'terms-of-service',
        'title': 'شروط الاستخدام والخدمة',
        'title_en': 'Terms of Service & EULA',
        'meta_description': 'شروط الاستخدام واتفاقية ترخيص المستخدم النهائي (EULA) لمنصة Trings',
        'sort_order': 2,
        'content': '''# شروط الاستخدام والخدمة (Terms of Service & EULA)
**تاريخ السريان:** 1 نوفمبر 2026

مرحباً بك في منصة **Trings (Trings SaaS)**. باستخدامك لموقعنا الإلكتروني، أو تطبيق الهاتف، أو خدمة **Apple App Clip**، فإنك توافق على الالتزام بشروط الاستخدام الموضحة أدناه واتفاقية ترخيص المستخدم النهائي القياسية لشركة أبل (**Standard Apple EULA**).

---

### 1. وصف الخدمة
تقدم منصة Trings نظاماً سحابياً متكاملاً لإدارة طوابير ونداء طلبات المطاعم والمقاهي، متيحةً استبدال أجهزة النداء البلاستيكية التقليدية ببيجر رقمي فوري يظهر في جوال العميل وشاشة القفل والجزيرة التفاعلية فور مسح كود الـ QR.

---

### 2. التراخيص والاستخدام المقبول
1. تمنحك المنصة ترخيصاً شخصياً، غير حصري، وغير قابل للتحويل لاستخدام تطبيق بيجر والـ App Clip وفقاً لهذه الشروط.
2. يُحظر تماماً:
   * محاولة التلاعب برموز الطلبات أو اختراق قنوات الاتصال بالخادم.
   * استخدام أدوات آلية أو روبوتات لتوليد طلبات وهمية أو إغراق شاشات الكاشير (DDoS/Spam).
   * إعادة هندسة الكود البرمجي أو استغلال أي ثغرة أمنية.

---

### 3. مسؤوليات المطاعم المشتركة (Merchants)
* يلتزم المطعم بصحة وجودة المنتجات المحضرة والالتزام بأوقات التحضير التقريبية المعلنة للعميل.
* تقع مسؤولية الحفاظ على سرية بيانات تسجيل دخول الكاشير والإدارة على عاتق المنشأة التجارية.

---

### 4. إخلاء المسؤولية وحدود الالتزام
* تُقدم الخدمة على أساس "كما هي" و"بحسب التوفر"، ونبذل قصارى جهدنا لضمان استمرارية الخوادم بنسبة 99.9%.
* لا تتحمل منصة Trings أي مسؤولية عن أي تأخير في تجهيز الطلبات ناتج عن المطاعم، أو انقطاع شبكة الإنترنت المحلية لدى المستخدم.

---

### 5. التعديل على الشروط والإنهاء
يحق لإدارة المنصة تحديث هذه الشروط عند الضرورة التقنية أو التنظيمية. استمرارك في استخدام التطبيق بعد أي تعديل يُعد قبولاً ضمنياً بالشروط المحدثة.

---

### 6. القانون المطبق والتواصل
تخضع هذه الاتفاقية للقوانين والأنظمة المعمول بها. لأي نزاع أو استفسار قانوني:
* **البريد القانوني:** legal@rahapp.com
* **الدعم الفني:** support@rahapp.com''',

        'content_en': '''# Terms of Service & End User License Agreement (EULA)
**Effective Date:** November 1, 2026

Welcome to **Trings SaaS**. By accessing our web portals, downloading our iOS Application, or using our **Apple App Clip**, you enter into a binding agreement governed by these Terms and the **Apple Standard Licensed Application End User License Agreement (EULA)**.

---

### 1. Nature of the Service
Trings provides an intelligent digital paging platform for cafes and food establishments, eliminating mechanical hardware pagers in favor of instant mobile notifications delivered via Web, App Clips, and iOS Dynamic Island / Live Activities.

---

### 2. Grant of License & Acceptable Use
1. You are granted a limited, non-exclusive, non-transferable revocable license to utilize the client software for receiving queue and order updates.
2. You agree not to:
   * Forge or reverse-engineer order tokens or intercept WebSocket communications.
   * Transmit automated requests, spam, or denial-of-service floods targeting merchant cashiers.
   * Attempt unauthorized access to merchant dashboards or super administrator portals.

---

### 3. Merchant Obligations
Partner restaurants and cafes remain entirely responsible for food safety, order fulfillment accuracy, and maintaining the confidentiality of their cashier and portal login credentials.

---

### 4. Disclaimer of Warranties & Limitation of Liability
* Trings is provided on an "AS IS" and "AS AVAILABLE" basis without warranties of any kind.
* In no event shall Trings SaaS or its operators be held liable for indirect, punitive, or consequential damages resulting from restaurant preparation delays or mobile carrier downtime.

---

### 5. Governing Law & Dispute Resolution
These Terms are governed by applicable trade and digital commerce laws. For contractual or legal inquiries:
* **Legal Affairs:** legal@rahapp.com
* **General Inquiries:** support@rahapp.com'''
    },
    {
        'slug': 'about-us',
        'title': 'عن منصة Trings',
        'title_en': 'About Trings SaaS',
        'meta_description': 'تعرف على قصة ورؤية منصة Trings للضيافة والمطاعم',
        'sort_order': 3,
        'content': '''# عن منصة Trings (About Trings SaaS)

تأسست **بيجر (Trings SaaS)** لإعادة ابتكار تجربة استلام الطلبات في المقاهي والمطاعم وصالات الطعام السريعة، والانتقال بقطاع الضيافة من الأجهزة البلاستيكية القديمة إلى عالم التجربة الرقمية اللحظية.

---

###  رؤيتنا
إنهاء عصر أجهزة البيجر التقليدية (Buzzer Pagers) التي تكلف آلاف الريالات، وتتعرض للتلف والضياع وتتطلب شحناً مستمراً، واستبدالها بنظام سحابي أنيق يعمل مباشرة عبر **هاتف العميل نفسه** دون أي تعقيد.

---

###  لماذا بيجر هو الخيار الأول؟
1. **بدون تحميل تطبيقات (Apple App Clip):** يمسح العميل الـ QR من شاشة الكاشير أو الطاولة، فيفتح له الكارت فورياً في نصف ثانية دون حاجة للانتظار في متجر التطبيقات.
2. **الجزيرة التفاعلية وشاشة القفل (Live Activities):** يتابع العميل حالة تحضير طلبه أولاً بأول بينما يتصفح هاتفه بحرية تامة.
3. **تعدد الفروع والمتاجر (Multi-Tenant SaaS):** واجهة مخصصة لكل مطعم بهويته وألوانه وشعاره الخاص، مع شاشات كاشير لحظية ولوحة إحصائيات متطورة.
4. **صديق للبيئة وموفر للتكاليف:** يقلل النفايات الإلكترونية بنسبة 100% ويوفر على أصحاب المقاهي تكاليف الصيانة والاستبدال الدورية للأجهزة الميكانيكية.

---

###  المقر والتواصل
* **الموقع الرسمي:** https://pager.rahapp.com
* **شراكات الأعمال والمطاعم:** partners@rahapp.com''',

        'content_en': '''# About Trings SaaS

**Trings SaaS** is an advanced hospitality tech solution built to reinvent how modern cafes, quick-service restaurants, and food courts communicate order readiness to their guests.

---

###  Our Mission
To retire bulky, fragile, expensive hardware buzzer pagers and replace them with instantaneous, frictionless digital paging directly on customers' smartphones via **Apple App Clips** and **Dynamic Island Live Activities**.

---

###  Key Advantages
1. **Zero Download Friction:** Guests scan the QR code and immediately access their digital pager within 0.5 seconds via Apple App Clip.
2. **Always-On Visibility:** Queue progress and preparation status remain visible on the Lock Screen and Dynamic Island without keeping the app open.
3. **Multi-Tenant White-Labeling:** Every restaurant enjoys its custom brand colors, icons, dedicated cashier terminals, and real-time analytics.
4. **Eco-Friendly & Cost-Saving:** 100% elimination of plastic electronic waste and zero maintenance costs associated with lost or broken buzzers.

---

###  Headquarters & Business Inquiries
* **Official Website:** https://pager.rahapp.com
* **Enterprise Partnerships:** partners@rahapp.com'''
    },
    {
        'slug': 'contact-support',
        'title': 'الدعم الفني والتواصل',
        'title_en': 'Customer Support & Help Desk',
        'meta_description': 'تواصل مع فريق الدعم الفني والمساعدة لخدمة Trings',
        'sort_order': 4,
        'content': '''# الدعم الفني والمساعدة (Help & Customer Support)

فريق الدعم الفني في منصة **بيجر (Trings SaaS)** متواجد على مدار الساعة لمساعدة أصحاب المطاعم والعملاء وضمان استقرار الخدمة وسلاسة استلام الطلبات.

---

###  قنوات التواصل المباشرة
* **البريد الإلكتروني للدعم الفني:** support@rahapp.com
* **مبيعات المطاعم والاشتراكات:** sales@rahapp.com
* **الاستفسارات العامة:** info@rahapp.com
* **الخط الساخن للمشتركين:** +966 50 000 0000

---

### ⏱️ أوقات العمل والاستجابة
* **مراقبة الخوادم والأنظمة:** تعمل على مدار الساعة 24/7/365 بنسبة جاهزية 99.9%.
* **فريق الدعم المباشر:** متاح يومياً من 8:00 صباحاً حتى 12:00 منتصف الليل بتوقيت مكة المكرمة.
* **متوسط زمن الرد على التذاكر:** أقل من ساعتين عمل.

---

###  الأسئلة الشائعة (FAQ)
* **هل يحتاج العميل إلى تحميل تطبيق؟**  
  لا، يعمل النظام تلقائياً عبر متصفح الجوال أو الـ Apple App Clip فور مسح رمز الـ QR.
* **كيف يتم تنبيه العميل عند جهوزية طلبه؟**  
  يهتز الهاتف ويرن بصوت مميز، بالإضافة إلى وميض في شاشة القفل والجزيرة التفاعلية (Dynamic Island).
* **كيف يمكنني تسجيل مطعمي في المنصة؟**  
  تواصل مع فريق المبيعات عبر sales@rahapp.com أو اطلب تفعيل حسابك من إدارة المنصة فورياً.''',

        'content_en': '''# Customer Support & Help Desk

The **Trings SaaS** support team is available around the clock to support restaurant partners and end-users with seamless order notification services.

---

###  Direct Communication Channels
* **Technical Support:** support@rahapp.com
* **Sales & Enterprise Onboarding:** sales@rahapp.com
* **General Inquiries:** info@rahapp.com
* **Support Hotline:** +966 50 000 0000

---

### ⏱️ Operating Hours & Service Level Agreement (SLA)
* **Infrastructure Monitoring:** 24/7/365 active telemetry with 99.9% uptime commitment.
* **Support Desk:** Open daily from 8:00 AM to 12:00 AM AST.
* **Average Ticket Resolution:** Under 2 business hours.

---

###  Frequently Asked Questions (FAQ)
* **Does the diner need to download an app?**  
  No. Scanning the counter QR code immediately opens the web pager or native Apple App Clip in seconds.
* **How is the customer alerted?**  
  Their smartphone sounds an audible chime, provides haptic vibration feedback, and updates the Dynamic Island and Lock Screen.
* **How can a restaurant join Trings SaaS?**  
  Contact our onboarding team at sales@rahapp.com or log into your merchant management portal.'''
    }
]

with conn.cursor() as cur:
    for p in pages:
        cur.execute('''
            INSERT INTO custom_pages (slug, title, title_en, content, content_en, meta_description, is_published, show_in_footer, sort_order)
            VALUES (%s, %s, %s, %s, %s, %s, 1, 1, %s)
            ON DUPLICATE KEY UPDATE
                title = VALUES(title),
                title_en = VALUES(title_en),
                content = VALUES(content),
                content_en = VALUES(content_en),
                meta_description = VALUES(meta_description),
                is_published = VALUES(is_published),
                show_in_footer = VALUES(show_in_footer),
                sort_order = VALUES(sort_order);
        ''', (p['slug'], p['title'], p['title_en'], p['content'], p['content_en'], p['meta_description'], p['sort_order']))
    conn.commit()
    print('Seeded clean pages successfully Count:', len(pages))
conn.close()

