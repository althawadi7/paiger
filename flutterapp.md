# 📱 تطبيق بيجر الذكي (Paiger App & App Clip Architecture)

دليل شامل وعملي لمطور فلاتر (**Flutter Developer**) لبناء تطبيق **Flutter** الحقيقي، والذي يجمع بين **تطبيق كامل قابل للتحميل من App Store / Google Play**، وفي الوقت نفسه يعمل كـ **App Clip فوري بنقرة واحدة عبر الـ QR Code بدون تحميل**.

السيرفر الآن يعمل بالكامل ومتصل بقاعدة بيانات **MySQL (MariaDB)** ومربوط بالدومين الرسمي المعتمد بشهادة أمان SSL:
🌐 **عنوان السيرفر للإنتاج:** `https://pager.rahapp.com`

---

## 📌 جدول المحتويات
1. [معمارية التطبيق المزدوج (Dual Architecture)](#1-معمارية-التطبيق-المزدوج)
2. [بيانات السيرفر ونقاط الاتصال (Endpoints & Base URLs)](#2-بيانات-السيرفر-ونقاط-الاتصال)
3. [دليل الـ REST APIs التفصيلي مع نماذج الـ JSON](#3-دليل-الـ-rest-apis)
4. [بروتوكول الـ WebSocket المباشر (Realtime Sync)](#4-بروتوكول-الـ-websocket-المباشر)
5. [أذونات الجوال وإعدادات الـ Info.plist & Manifest](#5-أذونات-الجوال-وإعدادات-الـ-plist)
6. [إعدادات Apple App Clip و Universal Links](#6-إعدادات-apple-app-clip)
7. [أكواد Flutter جاهزة للاستخدام المباشر (Models, Services, Scanner)](#7-أكواد-flutter-جاهزة)
8. [كود Swift لويدجت الـ Live Activity والجزيرة التفاعلية (Dynamic Island)](#8-كود-swift-للـ-live-activity)

---

## 1. معمارية التطبيق المزدوج

```
                          ┌──────────────────────────┐
                          │    رمز الاستجابة السريع   │
                          │        (QR Code)         │
                          │   pager.rahapp.com/pager/│
                          └─────────────┬────────────┘
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 ▼                                             ▼
      【مسح بكاميرا الآيفون العادية】                  【فتح من داخل التطبيق الكامل】
   (مستخدم لا يملك التطبيق أو زائر سريع)            (مستخدم حمّل التطبيق مسبقاً)
                 │                                             │
                 ▼                                             ▼
        [Apple App Clip]                                 [Full App]
   • يفتح في 0.5 ثانية بدون تثبيت                • يدعم ماسح QR سريع داخل التطبيق
   • يطلق Live Activity فورية على الشاشة         • أرشيف الطلبات السابقة في المقاهي
   • يطلق ويدجت الجزيرة التفاعلية                • تنبيهات متقدمة وإشعارات
   • بدون أي تسجيل دخول إطلاقاً (No Auth)        • واجهة مستخدم مخصصة للعميل فقط
```

---

## 2. بيانات السيرفر ونقاط الاتصال

| العنصر | القيمة |
| :--- | :--- |
| **الدومين الأساسي (Production Domain)** | `https://pager.rahapp.com` |
| **لوحة الإدارة العامة (Super Admin)** | `https://pager.rahapp.com/admin` |
| **بوابة دخول المطاعم (Merchant Portal)** | `https://pager.rahapp.com/login` |
| **رابط كاشير المطعم المخصص** | `https://pager.rahapp.com/r/{slug}/cashier` |
| **رابط ستاند الطباعة للمطعم** | `https://pager.rahapp.com/r/{slug}/print` |
| **رابط الـ WebSocket المباشر** | `wss://pager.rahapp.com/ws/pager/{token}` |
| **رابط التحقق من صحة أبل (AASA)** | `https://pager.rahapp.com/.well-known/apple-app-site-association` |
| **تنسيق رابط العميل عند المسح** | `https://pager.rahapp.com/pager/{token}` |
| **استخراج رمز الطلب (Token)** | هو المعرف الفريد النصي المكون من 10 خانات في نهاية الرابط (مثال: `76afaf526b`) |

---

### 🔑 بيانات الدخول الافتراضية للنظام السحابي (SaaS Accounts)

#### 1. حساب الإدارة العامة (Super Admin):
- **الرابط:** `https://pager.rahapp.com/admin/login`
- **اسم المستخدم:** `admin` (أو `admin@pager.rahapp.com`)
- **كلمة المرور:** `AdminPager@2026!`
- **الصلاحيات:** إنشاء المطاعم، إدارة الهوية والألوان والشعارات، متابعة جميع الطلبات الحية في المنصة لحظياً.

#### 2. حساب المطعم التجريبي (Demo Restaurant):
- **الرابط:** `https://pager.rahapp.com/login`
- **اسم المستخدم / البريد:** `store@pager.rahapp.com` (أو `main`)
- **كلمة المرور:** `Restaurant@2026!`
- **رابط شاشة الكاشير المباشر:** `https://pager.rahapp.com/r/main/cashier`
- **رابط ستاند الكاونتر للطباعة:** `https://pager.rahapp.com/r/main/print`

---

## 3. دليل الـ REST APIs

### 1) جلب تفاصيل الطلب الكاملة (Order Details)
يُستخدم فور فتح شاشة البيجر (أو فتح الـ App Clip) لجلب حالة الطلب والطابور:
- **الطريقة:** `GET`
- **المسار:** `/api/orders/{token}/details`
- **مثال الطلب:**
```bash
curl -s https://pager.rahapp.com/api/orders/76afaf526b/details
```
- **نموذج الاستجابة الناجحة (`200 OK`):**
```json
{
  "id": 10,
  "order_number": "42",
  "customer_name": "أحمد",
  "notes": "بدون سكر",
  "status": "waiting",
  "token": "76afaf526b",
  "restaurant_name": "بارنز كافيه (Barns)",
  "brand_color": "#C05621",
  "brand_icon": "cup.and.saucer.fill",
  "call_count": 0,
  "created_at": "2026-11-01T20:15:25",
  "called_at": null,
  "completed_at": null,
  "orders_ahead": 2,
  "estimated_wait": "6 دقائق",
  "avg_prep_mins": 3.0,
  "theme": "light"
}
```

حالات الطلب (`status`):
* `"waiting"`: الطلب قيد التحضير في المطبخ/البار.
* `"ringing"`: الكاشير يضغط زر الرنين 🔔 (الطلب جاهز للاستلام الآن!).
* `"onway"`: العميل ضغط زر "أنا في طريقي للاستلام".
* `"done"`: تم تسليم الطلب للعميل بنجاح.

---

### 2) إرسال إشعار "أنا في طريقي للاستلام" (Customer On-the-Way)
يستدعيه التطبيق عندما يضغط العميل على الزر:
- **الطريقة:** `POST`
- **المسار:** `/api/orders/{token}/onway`
- **مثال الطلب:**
```bash
curl -s -X POST https://pager.rahapp.com/api/orders/76afaf526b/onway
```
- **نموذج الاستجابة (`200 OK`):**
```json
{
  "id": 10,
  "order_number": "42",
  "customer_name": "أحمد",
  "token": "76afaf526b",
  "status": "onway"
}
```

---

### 3) تسجيل توكن ويدجت شاشة القفل (Register Live Activity Push Token)
تستدعيه مكتبة Swift / ActivityKit لحفظ رمز الـ APNs الخاص بالـ Live Activity بالسيرفر لتحديث شاشة القفل عن بُعد:
- **الطريقة:** `POST`
- **المسار:** `/api/orders/{token}/live-activity-token`
- **Headers:** `Content-Type: application/json`
- **جسم الطلب (Body):**
```json
{
  "push_token": "a1b2c3d4e5f6...64char_apns_hex_string",
  "device_type": "ios_live_activity"
}
```
- **نموذج الاستجابة (`200 OK`):**
```json
{
  "status": "ok",
  "message": "Live Activity token registered successfully"
}
```

---

### 4) جلب صورة كود الـ QR برمجياً
- **الطريقة:** `GET`
- **المسار:** `/api/qr/{token}`
- يُرجع صورة PNG مباشرة بدقة عالية يمكن عرضها في أي Widget أو مشاركتها.

---

## 4. بروتوكول الـ WebSocket المباشر

تعتمد شاشة البيجر على قناة WebSocket واحدة خفيفة وسريعة جداً لمزامنة رنين الكاشير وتحديث الطابور فوراً بدون تأخير:

🔗 **رابط الاتصال:**
```text
wss://pager.rahapp.com/ws/pager/{token}
```

### الرسائل الواردة من السيرفر (Server -> App):

#### 1. عند نجاح الاتصال (`connected`):
```json
{
  "event": "connected",
  "order": {
    "id": 10,
    "order_number": "42",
    "status": "waiting"
  },
  "queue_info": {
    "orders_ahead": 1,
    "estimated_wait": "3 دقائق"
  },
  "theme": "light"
}
```

#### 2. عند رنين الكاشير (`order_ringing`) 🔔:
```json
{
  "event": "order_ringing",
  "order": {
    "id": 10,
    "order_number": "42",
    "status": "ringing"
  }
}
```
> ⚡ **إجراء التطبيق:** تشغيل نغمة الرنين المستمرة، تشغيل الاهتزاز (Haptic Engine)، إضاءة الشاشة باللون الأحمر النابض، وإظهار زر كبير: "أنا قادم للاستلام 🏃‍♂️".

#### 3. عند ضغط "أنا في طريقي" (`order_onway`):
```json
{
  "event": "order_onway",
  "order": { "status": "onway" }
}
```

#### 4. عند تسليم الطلب واكتماله (`order_done`):
```json
{
  "event": "order_done",
  "order": { "status": "done" }
}
```

#### 5. عند حذف الطلب من الكاشير (`order_deleted`):
```json
{
  "event": "order_deleted"
}
```

### الرسائل الصادرة من التطبيق إلى السيرفر (App -> Server):
- إشعار الكاشير بأن العميل في طريقه:
```json
{ "action": "onway" }
```
- إرسال نبضة بقاء الاتصال (Keep-Alive Ping كل 25 ثانية):
```json
{ "action": "ping" }
```

---

## 5. أذونات الجوال وإعدادات الـ plist

### أ) إعدادات iOS (`ios/Runner/Info.plist`):
```xml
<!-- 1. إذن الكاميرا لمسح كود البيجر -->
<key>NSCameraUsageDescription</key>
<string>نحتاج إذن الكاميرا لمسح رمز الـ QR الخاص بطلبك ومتابعة دورك في الطابور.</string>

<!-- 2. تفعيل ويدجت شاشة القفل والجزيرة التفاعلية (ActivityKit) -->
<key>NSSupportsLiveActivities</key>
<true/>
<key>NSSupportsLiveActivitiesFrequentUpdates</key>
<true/>

<!-- 3. تشغيل الأصوات والتنبيهات الخلفية -->
<key>UIBackgroundModes</key>
<array>
    <string>remote-notification</string>
</array>
```

### ب) إعدادات Android (`android/app/src/main/AndroidManifest.xml`):
```xml
<!-- إذن الكاميرا والاهتزاز -->
<uses-permission android:name="android.permission.CAMERA" />
<uses-permission android:name="android.permission.VIBRATE" />
<uses-permission android:name="android.permission.INTERNET" />
```

---

## 6. إعدادات Apple App Clip و Universal Links

السيرفر يقدم ملف الارتباط الرسمي تلقائياً عبر:
`https://pager.rahapp.com/.well-known/apple-app-site-association`

### في مشروع Xcode:
1. في الـ **Signing & Capabilities** لكل من:
   - التطبيق الرئيسي (`Runner Target`)
   - الـ App Clip (`PagerClip Target`)
2. أضف قدرة: **Associated Domains**
3. أضف النطاقات التالية:
   - للتطبيق الكامل: `applinks:pager.rahapp.com`
   - للـ App Clip: `appclips:pager.rahapp.com`

بمجرد توجيه كاميرا الآيفون لرابط مثل:
`https://pager.rahapp.com/pager/76afaf526b`
سيتعرف نظام iOS على الرابط فوراً ويعرض بطاقة الـ **App Clip** بدون الحاجة لأي متصفح!

---

## 7. أكواد Flutter جاهزة

### 1. مكتبات `pubspec.yaml` المقترحة:
```yaml
dependencies:
  flutter:
    sdk: flutter
  http: ^1.2.1
  web_socket_channel: ^3.0.0
  mobile_scanner: ^5.1.1       # لماسح الـ QR السريع
  vibration: ^2.0.1           # للاهتزاز القوي
  audioplayers: ^6.0.0        # لصوت الرنين
  google_fonts: ^6.2.1        # لخط Readex Pro
  shared_preferences: ^2.2.3  # لحفظ سجل الطلبات السابقة
```

---

### 2. نموذج بيانات الطلب (`lib/models/order_model.dart`):
```dart
class OrderModel {
  final int id;
  final String orderNumber;
  final String customerName;
  final String notes;
  final String status; // waiting, ringing, onway, done
  final String token;
  final String restaurantName;
  final String brandColor;
  final String brandIcon;
  final int ordersAhead;
  final String estimatedWait;

  OrderModel({
    required this.id,
    required this.orderNumber,
    required this.customerName,
    required this.notes,
    required this.status,
    required this.token,
    required this.restaurantName,
    required this.brandColor,
    required this.brandIcon,
    required this.ordersAhead,
    required this.estimatedWait,
  });

  bool get isWaiting => status == 'waiting';
  bool get isRinging => status == 'ringing';
  bool get isOnWay => status == 'onway';
  bool get isDone => status == 'done';

  factory OrderModel.fromJson(Map<String, dynamic> json) {
    return OrderModel(
      id: json['id'] ?? 0,
      orderNumber: (json['order_number'] ?? '').toString(),
      customerName: json['customer_name'] ?? '',
      notes: json['notes'] ?? '',
      status: json['status'] ?? 'waiting',
      token: json['token'] ?? '',
      restaurantName: json['restaurant_name'] ?? 'بيجر الطلبات',
      brandColor: json['brand_color'] ?? '#C05621',
      brandIcon: json['brand_icon'] ?? 'cup.and.saucer.fill',
      ordersAhead: json['orders_ahead'] ?? 0,
      estimatedWait: json['estimated_wait'] ?? '',
    );
  }
}
      id: json['id'] ?? 0,
      orderNumber: (json['order_number'] ?? '').toString(),
      customerName: json['customer_name'] ?? '',
      notes: json['notes'] ?? '',
      status: json['status'] ?? 'waiting',
      token: json['token'] ?? '',
      ordersAhead: json['orders_ahead'] ?? 0,
      estimatedWait: json['estimated_wait'] ?? '',
    );
  }
}
```

---

### 3. خدمة الاتصال بالسيرفر (`lib/services/pager_api_service.dart`):
```dart
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/order_model.dart';

class PagerApiService {
  static const String baseUrl = 'https://pager.rahapp.com';

  static Future<OrderModel?> fetchOrderDetails(String token) async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/api/orders/$token/details'));
      if (response.statusCode == 200) {
        return OrderModel.fromJson(json.decode(utf8.decode(response.bodyBytes)));
      }
    } catch (e) {
      print('Error fetching order: $e');
    }
    return null;
  }

  static Future<bool> sendOnWay(String token) async {
    try {
      final response = await http.post(Uri.parse('$baseUrl/api/orders/$token/onway'));
      return response.statusCode == 200;
    } catch (e) {
      print('Error sending onway: $e');
      return false;
    }
  }

  static Future<bool> registerLiveActivityToken(String orderToken, String pushToken) async {
    try {
      final response = await http.post(
        Uri.parse('$baseUrl/api/orders/$orderToken/live-activity-token'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'push_token': pushToken, 'device_type': 'ios_live_activity'}),
      );
      return response.statusCode == 200;
    } catch (e) {
      print('Error registering live activity token: $e');
      return false;
    }
  }
}
```

---

### 4. شاشة ماسح الـ QR السريعة (`lib/views/qr_scanner_view.dart`):
```dart
import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'pager_screen.dart';

class QrScannerView extends StatefulWidget {
  const QrScannerView({Key? key}) : super(key: key);

  @override
  State<QrScannerView> createState() => _QrScannerViewState();
}

class _QrScannerViewState extends State<QrScannerView> {
  bool _scanned = false;

  void _onDetect(BarcodeCapture capture) {
    if (_scanned) return;
    final List<Barcode> barcodes = capture.barcodes;
    for (final barcode in barcodes) {
      final rawUrl = barcode.rawValue;
      if (rawUrl != null && rawUrl.contains('/pager/')) {
        setState(() => _scanned = true);
        
        // استخراج الـ token من الرابط https://pager.rahapp.com/pager/{token}
        final uri = Uri.parse(rawUrl);
        final segments = uri.pathSegments;
        final tokenIndex = segments.indexOf('pager');
        if (tokenIndex != -1 && tokenIndex + 1 < segments.length) {
          final token = segments[tokenIndex + 1];
          Navigator.pushReplacement(
            context,
            MaterialPageRoute(builder: (_) => PagerScreen(token: token)),
          );
          break;
        }
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF24160F), // Espresso Dark
      appBar: AppBar(
        title: const Text('امسح كود الطلب', style: TextStyle(fontFamily: 'Readex Pro')),
        backgroundColor: Colors.transparent,
        elevation: 0,
      ),
      body: Stack(
        alignment: Alignment.center,
        children: [
          MobileScanner(onDetect: _onDetect),
          Container(
            width: 260,
            height: 260,
            decoration: BoxDecoration(
              border: Border.all(color: const Color(0xFFC05621), width: 3),
              borderRadius: BorderRadius.circular(24),
            ),
          ),
          const Positioned(
            bottom: 60,
            child: Text(
              'وجه الكاميرا نحو كود الـ QR على الفاتورة',
              style: TextStyle(color: Colors.white70, fontFamily: 'Readex Pro', fontSize: 14),
            ),
          )
        ],
      ),
    );
  }
}
```

---

## 8. كود Swift للـ Live Activity والجزيرة التفاعلية

لتشغيل الودجت في مجلد `ios/PagerWidget/PagerLiveActivity.swift`:

```swift
import ActivityKit
import WidgetKit
import SwiftUI

public struct PagerAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var restaurantName: String // "بارنز كافيه (Barns)"
        public var brandColor: String     // "#C05621"
        public var brandIcon: String      // "cup.and.saucer.fill" أو "takeoutbag.and.cup.and.straw.fill"
        public var orderNumber: String    // "42"
        public var status: String         // "waiting", "ringing", "onway", "done"
        public var ordersAhead: Int       // 2
        public var estimatedWait: String  // "6 دقائق"
    }
    public var token: String
}

@main
struct PagerWidgetBundle: WidgetBundle {
    var body: some Widget {
        PagerLiveActivity()
    }
}

struct PagerLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: PagerAttributes.self) { context in
            // 📱 شاشة القفل (Lock Screen Widget بتصميم الإسبريسو الفاخر واسم المطعم)
            VStack(alignment: .leading, spacing: 10) {
                // شريط رأس المطعم المخصص
                HStack {
                    Image(systemName: context.state.brandIcon)
                        .foregroundColor(Color(hex: context.state.brandColor))
                        .imageScale(.medium)
                    Text(context.state.restaurantName)
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(Color(hex: "24160F"))
                    Spacer()
                    if context.state.status == "waiting" {
                        Text(context.state.estimatedWait)
                            .font(.system(size: 13, weight: .semibold))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color(hex: context.state.brandColor).opacity(0.12))
                            .foregroundColor(Color(hex: context.state.brandColor))
                            .cornerRadius(8)
                    }
                }
                
                Divider().background(Color(hex: "E5DED3"))

                HStack(alignment: .center) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("طلب رقم #\(context.state.orderNumber)")
                            .font(.system(size: 24, weight: .heavy))
                            .foregroundColor(Color(hex: "24160F"))
                        
                        Text(context.state.status == "ringing" ? "🔔 جاهز للاستلام الآن! تفضل للكاشير" : "قيد التحضير بعناية...")
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: "7C6958"))
                    }
                    Spacer()
                    if context.state.status == "waiting" && context.state.ordersAhead > 0 {
                        VStack(alignment: .trailing) {
                            Text("\(context.state.ordersAhead)")
                                .font(.system(size: 20, weight: .bold))
                                .foregroundColor(Color(hex: "24160F"))
                            Text("أشخاص قبلك")
                                .font(.system(size: 11))
                                .foregroundColor(Color(hex: "7C6958"))
                        }
                    }
                }
            }
            .padding(16)
            .background(Color(hex: "F7F4EE")) // Crema Latte background
        } dynamicIsland: { context in
            // 🏝️ الجزيرة التفاعلية (Dynamic Island)
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 5) {
                        Image(systemName: context.state.brandIcon)
                            .foregroundColor(Color(hex: context.state.brandColor))
                        Text("#\(context.state.orderNumber)")
                            .font(.headline)
                            .bold()
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.state.status == "ringing" ? "جاهز! 🔔" : context.state.estimatedWait)
                        .font(.subheadline)
                        .foregroundColor(context.state.status == "ringing" ? .red : .white)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    HStack {
                        Text(context.state.restaurantName)
                            .font(.caption)
                            .foregroundColor(.gray)
                        Spacer()
                        Text(context.state.status == "ringing" ? "تفضل بالاستلام الآن 🏃‍♂️" : "متبقي \(context.state.ordersAhead) قبلك")
                            .font(.caption)
                            .bold()
                            .foregroundColor(context.state.status == "ringing" ? .yellow : .white)
                    }
                }
            } compactLeading: {
                Image(systemName: context.state.brandIcon)
                    .foregroundColor(Color(hex: context.state.brandColor))
            } compactTrailing: {
                Text(context.state.status == "ringing" ? "🔔" : "#\(context.state.orderNumber)")
                    .foregroundColor(context.state.status == "ringing" ? .red : .white)
                    .bold()
            } minimal: {
                Image(systemName: context.state.status == "ringing" ? "bell.badge.fill" : context.state.brandIcon)
                    .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: context.state.brandColor))
            }
        }
    }
}

extension Color {
    init(hex: String) {
        let scanner = Scanner(string: hex)
        var rgbValue: UInt64 = 0
        scanner.scanHexInt64(&rgbValue)
        let r = Double((rgbValue & 0xFF0000) >> 16) / 255.0
        let g = Double((rgbValue & 0x00FF00) >> 8) / 255.0
        let b = Double(rgbValue & 0x0000FF) / 255.0
        self.init(red: r, green: g, blue: b)
    }
}
```

---

## 🎯 ملخص رحلة العميل بالتطبيق (Full Summary)
1. **العميل يمسح الـ QR** ⬅️ يفتح الـ App Clip فوراً بثوانٍ بدون تحميل، أو يفتح التطبيق مباشرة إن كان مثبتاً.
2. **الـ App Clip يطلق الـ Live Activity** ⬅️ تظهر حالة الطلب على شاشة القفل والجزيرة التفاعلية ويُرسل التوكن للسيرفر.
3. **الكاشير يضغط زر الرنين 🔔** ⬅️ يستقبل السيرفر الطلب، فيرسل WebSocket فوري لشاشة العميل مع إشعار APNs لتنبيه الهاتف برنين قوي واهتزاز.
4. **العميل يضغط "أنا في طريقي"** ⬅️ ينعكس إشعار صوتي ومرئي فوري في شاشة الكاشير بأن العميل متوجه للاستلام.
5. **تسليم الطلب** ⬅️ تنتهي الجلسة ويُحفظ الطلب في سجل الطلبات السابقة في التطبيق الكامل.
