# 📱 الدليل الشامل لمطور Flutter | تطبيق بيجر الذكي والـ App Clip (Paiger SaaS)

دليل هندسي وعملي شامل وموثق لمطور فلاتر (**Flutter Developer**) لبناء التطبيق ورفعه لمتجر التطبيقات (**App Store & Google Play**) بنجاح من المرة الأولى، مع ربطه الكامل بنظامنا السحابي **SaaS**، وتفعيل تقنيات **Apple App Clip** و **WidgetKit / ActivityKit** لظهور بطاقة الطلب في **شاشة القفل (Lock Screen)** و **الجزيرة التفاعلية (Dynamic Island)**.

🌐 **السيرفر السحابي المعتمد للإنتاج (Production Server):** `https://pager.rahapp.com`  
🗄️ **قاعدة البيانات:** `MariaDB / MySQL` (`pager_db` معزولة ومستقرة)  
🔒 **شهادة الأمان:** `Let's Encrypt SSL` نشطة ومعتمدة لدى خوادم Apple  
🆔 **Apple Developer Team ID:** `7V3J58823K`

---

## 📌 الفهرس
1. [معمارية الحزم والـ Bundle IDs ومتطلبات متجر آبل](#1-معمارية-الحزم-والـ-bundle-ids)
2. [طريقة الرفع لمتجر App Store Connect في أرشيف واحد](#2-طريقة-الرفع-لمتجر-app-store-connect)
3. [بيانات السيرفر ونقاط الاتصال والحسابات الحية](#3-بيانات-السيرفر-ونقاط-الاتصال)
4. [إعدادات Targets و Info.plist في Xcode](#4-إعدادات-targets-و-infoplist)
5. [كود Swift الكامل لويدجت شاشة القفل والجزيرة التفاعلية](#5-كود-swift-للويدجت-والـ-live-activity)
6. [كود Flutter الكامل (Deep Links, API, WebSocket, UI)](#6-كود-flutter-الكامل)
7. [قائمة الفحص والتحقق قبل الرفع لتجنب أي رفض](#7-قائمة-الفحص-والتحقق-قبل-الرفع)

---

## 1. معمارية الحزم والـ Bundle IDs

### ⚠️ قاعدة آبل الصارمة: هل الـ Bundle ID للتطبيق يختلف عن الـ App Clip؟
**نعم! ولا بد أن يبدأ الـ App Clip بمعرف التطبيق الرئيسي (Prefix):**

| المكوّن (Target) | المعرف المعتمد (Bundle Identifier) | الوظيفة |
| :--- | :--- | :--- |
| **التطبيق الرئيسي (Full App)** | `com.rahapp.pager` | التطبيق الكامل القابل للتحميل من App Store |
| **الكليب المصغر (App Clip)** | `com.rahapp.pager.Clip` | يفتح تلقائياً عند مسح الـ QR بدون أي تحميل |
| **الويدجت والـ ActivityKit** | `com.rahapp.pager.Widget` | يدير كارت شاشة القفل والـ Dynamic Island |

> [!IMPORTANT]
> **قاعدة آبل:** لا يمكن إطلاق App Clip ككيان مستقل في متجر التطبيقات. يتم إنشاؤه كـ **Target فرعي** مدمج داخل نفس مشروع Xcode للتطبيق الرئيسي، ويتم رفع ملف Archive واحد يجمعهما معاً.

---

## 2. طريقة الرفع لمتجر App Store Connect

أنت **لا تقوم برفع ملفين منفصلين**، بل تصدر أرشيفاً واحداً (`.xcarchive`) من Xcode يحتوي على كل شيء:

```
                            [ Xcode Project ]
                                    │
            ┌───────────────────────┴───────────────────────┐
            ▼                                               ▼
   [com.rahapp.pager]                             [com.rahapp.pager.Clip]
    (Full iOS App)                                      (App Clip)
            │                                               │
            └───────────────┬───────────────────────────────┘
                            │ (Embed App Clips Phase)
                            ▼
                    [ Single Archive ]
                 (com.rahapp.pager.ipa)
                            │
                            ▼
                 [ App Store Connect ]
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
    صفحة التطبيق الرئيسية             قسم الـ App Clip التلقائي
 (App Store Product Page)        (Invocation URL: /pager/*)
```

### خطوات Xcode:
1. افتح المشروع في Xcode: `open ios/Runner.xcworkspace`.
2. تأكد من ضبط الـ Signing لجميع الـ Targets:
   - `Runner`: `com.rahapp.pager` (مع Provisioning Profile للتطبيق).
   - `PagerClip`: `com.rahapp.pager.Clip` (مع Provisioning Profile للـ Clip).
   - `PagerWidget`: `com.rahapp.pager.Widget` (مع Provisioning Profile للـ Widget).
3. في Target التطبيق الرئيسي (`Runner`):
   - اذهب إلى **Build Phases** ➔ اضغط `+` ➔ اختر **New Copy Files Build Phase**.
   - اختر الوجهة (Destination): **App Clips**.
   - أضف `PagerClip.app`. (في الإصدارات الحديثة يضيفها Xcode تلقائياً تحت اسم **Embed App Clips**).
4. اضغط في الأعلى: **Product ➔ Destination ➔ Any iOS Device (arm64)**.
5. اضغط: **Product ➔ Archive**.
6. في نافذة **Organizer**: اضغط **Distribute App** ➔ **App Store Connect** ➔ **Upload**.

### إعداد بطاقة الـ App Clip في App Store Connect:
1. ادخل على لوحة [App Store Connect](https://appstoreconnect.apple.com).
2. افتح الإصدار الجديد لتطبيقك (`1.0.0`).
3. انزل إلى قسم **App Clip** (يظهر تلقائياً بمجرد رفع الأرشيف).
4. املأ البيانات:
   - **Header Image:** صورة عالية الدقة للمطعم/البيجر (3000x2000px).
   - **Title (العنوان):** بيجر الطلبات الذكي
   - **Subtitle (الوصف):** تابع دورك فورياً بدون انتظار
   - **Action Button:** فتح (Open)
   - **URL Pattern (رابط التفعيل):**
     `https://pager.rahapp.com/pager`
     و
     `https://pager.rahapp.com/order`

---

## 3. بيانات السيرفر ونقاط الاتصال

| البند | الرابط / القيمة |
| :--- | :--- |
| **الدومين الأساسي (Base URL)** | `https://pager.rahapp.com` |
| **مسار مسح الـ QR للعميل** | `https://pager.rahapp.com/pager/{token}` أو `https://pager.rahapp.com/order/{token}` |
| **استعلام بيانات الطلب (REST)** | `GET https://pager.rahapp.com/api/orders/{token}/details` |
| **تسجيل توكن الـ Live Activity** | `POST https://pager.rahapp.com/api/orders/{token}/live-activity-token` |
| **إشعار "أنا قادم للاستلام"** | `POST https://pager.rahapp.com/api/orders/{token}/onway` |
| **البث المباشر (WebSocket)** | `wss://pager.rahapp.com/ws/pager/{token}` |
| **ملف التحقق الرسمي لآبل (AASA)** | `https://pager.rahapp.com/.well-known/apple-app-site-association` |

### 🔑 بيانات حسابات الـ SaaS للاختبار المباشر:

#### 1. حساب السوبر أدمن (Super Admin):
- **الرابط:** `https://pager.rahapp.com/admin`
- **المستخدم:** `admin` | **كلمة المرور:** `AdminPager@2026!`
- **الاستخدام:** مراقبة كافة الفروع، إنشاء مطاعم جديدة، تجربة رنين أي طلب.

#### 2. حساب المطعم الرئيسي التجريبي (Demo Restaurant):
- **الرابط:** `https://pager.rahapp.com/login`
- **المستخدم:** `store@pager.rahapp.com` | **كلمة المرور:** `Restaurant@2026!`
- **شاشة الكاشير المباشرة:** `https://pager.rahapp.com/r/main/cashier`
- **ستاند الكاونتر للطباعة:** `https://pager.rahapp.com/r/main/print`

---

## 4. إعدادات Targets و Info.plist

### أ. ملف `Runner/Info.plist` و `PagerClip/Info.plist`:
أضف السطور التالية لدعم الـ Live Activities والـ Associated Domains:

```xml
<!-- تفعيل الـ Live Activities في شاشة القفل والجزيرة التفاعلية -->
<key>NSSupportsLiveActivities</key>
<true/>
<key>NSSupportsLiveActivitiesFrequentUpdates</key>
<true/>

<!-- دعم الكاميرا لمسح الـ QR من داخل التطبيق -->
<key>NSCameraUsageDescription</key>
<string>نحتاج إذن الكاميرا لمسح باركود البيجر واستلام طلبك فورياً</string>

<!-- إعدادات إبقاء التطبيق نشطاً في الخلفية -->
<key>UIBackgroundModes</key>
<array>
    <string>remote-notification</string>
    <string>fetch</string>
</array>
```

### ب. ملف الـ Entitlements (`Runner.entitlements` و `PagerClip.entitlements`):
في كلا الملفين، فعّل الـ Associated Domains لربط الدومين مع الـ App Clip:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.developer.associated-domains</key>
    <array>
        <string>appclips:pager.rahapp.com</string>
        <string>applinks:pager.rahapp.com</string>
    </array>
</dict>
</plist>
```

---

## 5. كود Swift للويدجت والـ Live Activity

داخل مجلد الـ Widget Extension (`ios/PagerWidget/OrderLiveActivity.swift`)، ضع الكود التالي المعتمد:

```swift
import WidgetKit
import SwiftUI
import ActivityKit

// 1. هيكل البيانات للطلب
public struct OrderLiveActivityAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable {
        public var status: String       // waiting, ringing, onway, done
        public var title: String        // "طلبك قيد التحضير" أو "طلبك جاهز!"
        public var message: String      // تفاصيل إضافية
        public var estimatedMinutes: Int
        public var ordersAhead: Int
    }
    
    public var orderNumber: String
    public var restaurantName: String
    public var brandColorHex: String
    public var brandIcon: String
}

// 2. تصميم الويدجت في شاشة القفل والجزيرة التفاعلية
@main
struct PagerWidgetBundle: WidgetBundle {
    var body: some Widget {
        OrderLiveActivityWidget()
    }
}

struct OrderLiveActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: OrderLiveActivityAttributes.self) { context in
            // 📱 شاشة القفل (Lock Screen Banner - Espresso Latte Light Mode)
            HStack(spacing: 14) {
                // أيقونة الحالة الدائرية
                ZStack {
                    Circle()
                        .fill(context.state.status == "ringing" ? Color.red.opacity(0.18) : Color(hex: context.attributes.brandColorHex).opacity(0.15))
                        .frame(width: 50, height: 50)
                    
                    Image(systemName: context.state.status == "ringing" ? "bell.badge.fill" : "cup.and.saucer.fill")
                        .font(.system(size: 22, weight: .bold))
                        .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: context.attributes.brandColorHex))
                }
                
                // تفاصيل الطلب
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(context.attributes.restaurantName)
                            .font(.system(size: 13, weight: .bold))
                            .foregroundColor(Color(hex: "5A463A"))
                        Spacer()
                        Text("#\(context.attributes.orderNumber)")
                            .font(.system(size: 16, weight: .heavy, design: .rounded))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(Color(hex: context.attributes.brandColorHex).opacity(0.15))
                            .foregroundColor(Color(hex: context.attributes.brandColorHex))
                            .cornerRadius(6)
                    }
                    
                    Text(context.state.title)
                        .font(.system(size: 15, weight: .bold))
                        .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: "23150D"))
                    
                    Text(context.state.message)
                        .font(.system(size: 12))
                        .foregroundColor(Color(hex: "7A6656"))
                }
            }
            .padding(16)
            .background(Color(hex: "F6F3ED")) // لون اللاتيه الأصلي المعتمد
            .activityBackgroundTint(Color.white)
            
        } dynamicIsland: { context in
            // 🏝️ الجزيرة التفاعلية (Dynamic Island)
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 6) {
                        Image(systemName: context.state.status == "ringing" ? "bell.fill" : "clock.fill")
                            .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: context.attributes.brandColorHex))
                        Text("#\(context.attributes.orderNumber)")
                            .font(.headline)
                            .bold()
                    }
                    .padding(.leading, 6)
                }
                
                DynamicIslandExpandedRegion(.trailing) {
                    Text(context.attributes.restaurantName)
                        .font(.subheadline)
                        .fontWeight(.semibold)
                        .padding(.trailing, 6)
                }
                
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(context.state.title)
                            .font(.headline)
                            .foregroundColor(context.state.status == "ringing" ? .red : .primary)
                        Text(context.state.message)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 8)
                }
            } compactLeading: {
                Image(systemName: context.state.status == "ringing" ? "bell.badge.fill" : "cup.and.saucer.fill")
                    .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: context.attributes.brandColorHex))
            } compactTrailing: {
                Text("#\(context.attributes.orderNumber)")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundColor(context.state.status == "ringing" ? .red : .primary)
            } minimal: {
                Image(systemName: context.state.status == "ringing" ? "bell.badge.fill" : "bell.fill")
                    .foregroundColor(context.state.status == "ringing" ? .red : Color(hex: context.attributes.brandColorHex))
            }
        }
    }
}

// دالة مساعدة لتحويل Hex إلى Color
extension Color {
    init(hex: String) {
        let cleanHex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: cleanHex).scanHexInt64(&int)
        let r, g, b: UInt64
        switch cleanHex.count {
        case 6:
            (r, g, b) = ((int >> 16) & 0xFF, (int >> 8) & 0xFF, int & 0xFF)
        default:
            (r, g, b) = (192, 86, 33)
        }
        self.init(.sRGB, red: Double(r) / 255, green: Double(g) / 255, blue: Double(b) / 255, opacity: 1)
    }
}
```

---

## 6. كود Flutter الكامل

### أ. حزم `pubspec.yaml` المطلوبة:
```yaml
dependencies:
  flutter:
    sdk: flutter
  http: ^1.2.0
  web_socket_channel: ^3.0.0
  app_links: ^6.3.0          # لاستقبال روابط الـ App Clip والـ QR
  mobile_scanner: ^5.2.3     # لمسح باركود البيجر بكاميرا التطبيق
  audioplayers: ^6.0.0       # لتشغيل صوت رنين البيجر عند النداء
  vibration: ^2.0.0          # للاهتزاز القوي للعميل
```

---

### ب. نموذج بيانات الطلب (`lib/models/order_model.dart`):
```dart
class OrderModel {
  final int id;
  final String orderNumber;
  final String token;
  final String status; // waiting, ringing, onway, done
  final String customerName;
  final String notes;
  final String restaurantName;
  final String brandColor;
  final String brandIcon;
  final int elapsedMinutes;

  OrderModel({
    required this.id,
    required this.orderNumber,
    required this.token,
    required this.status,
    required this.customerName,
    required this.notes,
    required this.restaurantName,
    required this.brandColor,
    required this.brandIcon,
    required this.elapsedMinutes,
  });

  factory OrderModel.fromJson(Map<String, dynamic> json) {
    return OrderModel(
      id: json['id'] ?? 0,
      orderNumber: json['order_number']?.toString() ?? '',
      token: json['token'] ?? '',
      status: json['status'] ?? 'waiting',
      customerName: json['customer_name'] ?? '',
      notes: json['notes'] ?? '',
      restaurantName: json['restaurant_name'] ?? 'المطعم',
      brandColor: json['brand_color'] ?? '#C05621',
      brandIcon: json['brand_icon'] ?? 'coffee',
      elapsedMinutes: json['elapsed_minutes'] ?? 0,
    );
  }

  bool get isRinging => status == 'ringing';
  bool get isWaiting => status == 'waiting';
  bool get isOnWay => status == 'onway';
  bool get isDone => status == 'done';
}
```

---

### ج. خدمة الاتصال بالسيرفر والـ WebSocket (`lib/services/pager_api_service.dart`):
```dart
import 'dart:convert';
import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';
import '../models/order_model.dart';

class PagerApiService {
  static const String baseUrl = 'https://pager.rahapp.com';
  static const String wsUrl = 'wss://pager.rahapp.com';

  /// 1. استخراج الـ Token من أي رابط تم مسحه عبر الكاميرا
  static String? extractTokenFromUrl(String scannedUrl) {
    try {
      final uri = Uri.parse(scannedUrl.trim());
      final segments = uri.pathSegments;
      if (segments.contains('pager') || segments.contains('order')) {
        return segments.last;
      }
      return null;
    } catch (_) {
      return null;
    }
  }

  /// 2. جلب بيانات الطلب من السيرفر
  static Future<OrderModel?> fetchOrderDetails(String token) async {
    final res = await http.get(Uri.parse('$baseUrl/api/orders/$token/details'));
    if (res.statusCode == 200) {
      final data = jsonDecode(utf8.decode(res.bodyBytes));
      if (data['order'] != null) {
        return OrderModel.fromJson(data['order']);
      }
    }
    return null;
  }

  /// 3. إشعار الكاشير بأن العميل "في طريقه للاستلام"
  static Future<bool> notifyOnWay(String token) async {
    final res = await http.post(Uri.parse('$baseUrl/api/orders/$token/onway'));
    return res.statusCode == 200;
  }

  /// 4. تسجيل Push Token الخاص بالـ Live Activity لتحديث شاشة القفل تلقائياً
  static Future<void> registerLiveActivityToken(String orderToken, String pushToken) async {
    await http.post(
      Uri.parse('$baseUrl/api/orders/$orderToken/live-activity-token'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'push_token': pushToken,
        'device_type': 'ios_live_activity'
      }),
    );
  }

  /// 5. فتح قناة WebSocket للبث الحي اللحظي
  static WebSocketChannel connectOrderWebSocket(String token) {
    return WebSocketChannel.connect(Uri.parse('$wsUrl/ws/pager/$token'));
  }
}
```

---

### د. خدمة التحكم بالـ Live Activity في iOS (`lib/services/live_activity_manager.dart`):
```dart
import 'package:flutter/services.dart';
import '../models/order_model.dart';
import 'pager_api_service.dart';

class LiveActivityManager {
  static const MethodChannel _channel = MethodChannel('com.rahapp.pager/live_activity');

  /// بدء الـ Live Activity على شاشة القفل والـ Dynamic Island
  static Future<void> start(OrderModel order) async {
    try {
      final String? pushToken = await _channel.invokeMethod('startLiveActivity', {
        'orderNumber': order.orderNumber,
        'restaurantName': order.restaurantName,
        'brandColor': order.brandColor,
        'brandIcon': order.brandIcon,
        'orderToken': order.token,
      });

      if (pushToken != null && pushToken.isNotEmpty) {
        // إرسال الـ Push Token للسيرفر حتى يستطيع السيرفر تحديث الجزيرة التفاعلية عند رنين الكاشير
        await PagerApiService.registerLiveActivityToken(order.token, pushToken);
      }
    } on PlatformException catch (e) {
      print('Failed to start Live Activity: ${e.message}');
    }
  }

  /// إنهاء الـ Live Activity عند تسليم الطلب
  static Future<void> stop() async {
    try {
      await _channel.invokeMethod('stopLiveActivity');
    } catch (_) {}
  }
}
```

---

### هـ. كود ربط الـ Platform Channel في `AppDelegate.swift`:
داخل ملف `ios/Runner/AppDelegate.swift` و `ios/PagerClip/AppDelegate.swift`:

```swift
import UIKit
import Flutter
import ActivityKit

@UIApplicationMain
@objc class AppDelegate: FlutterAppDelegate {
  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    
    let controller : FlutterViewController = window?.rootViewController as! FlutterViewController
    let activityChannel = FlutterMethodChannel(name: "com.rahapp.pager/live_activity",
                                              binaryMessenger: controller.binaryMessenger)
    
    activityChannel.setMethodCallHandler({
      (call: FlutterMethodCall, result: @escaping FlutterResult) -> Void in
      if call.method == "startLiveActivity" {
        guard let args = call.arguments as? [String: Any],
              let orderNumber = args["orderNumber"] as? String,
              let restaurantName = args["restaurantName"] as? String,
              let brandColor = args["brandColor"] as? String,
              let brandIcon = args["brandIcon"] as? String else {
          result(FlutterError(code: "INVALID_ARGS", message: "Arguments missing", details: nil))
          return
        }
        
        if #available(iOS 16.1, *) {
          let attributes = OrderLiveActivityAttributes(
            orderNumber: orderNumber,
            restaurantName: restaurantName,
            brandColorHex: brandColor,
            brandIcon: brandIcon
          )
          let state = OrderLiveActivityAttributes.ContentState(
            status: "waiting",
            title: "طلبك قيد التحضير ⏳",
            message: "سنقوم بتنبيهك فور جاهزية طلبك للاستلام",
            estimatedMinutes: 5,
            ordersAhead: 0
          )
          
          do {
            let activity = try Activity<OrderLiveActivityAttributes>.request(
              attributes: attributes,
              contentState: state,
              pushType: .token
            )
            
            Task {
              for await tokenData in activity.pushTokenUpdates {
                let tokenString = tokenData.map { String(format: "%02x", $0) }.joined()
                result(tokenString)
                return
              }
            }
          } catch {
            result(FlutterError(code: "ACTIVITY_ERROR", message: error.localizedDescription, details: nil))
          }
        } else {
          result(nil)
        }
      } else if call.method == "stopLiveActivity" {
        if #available(iOS 16.1, *) {
          Task {
            for activity in Activity<OrderLiveActivityAttributes>.activities {
              await activity.end(dismissalPolicy: .immediate)
            }
          }
        }
        result(true)
      } else {
        result(FlutterMethodNotImplemented)
      }
    })

    GeneratedPluginRegistrant.register(with: self)
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
}
```

---

## 7. قائمة الفحص والتحقق قبل الرفع لتجنب أي رفض

| العنصر | الإجراء الإلزامي | الحالة |
| :--- | :--- | :---: |
| **حجم الـ App Clip** | يجب ألا يتجاوز حجم الـ App Clip غير المضغوط **15 ميجابايت** (تجنب تضمين خطوط ثقيلة أو فيديوهات داخل الـ Clip target). | ✅ |
| **تطابق الـ Associated Domains** | تأكد من وجود `appclips:pager.rahapp.com` في ملف `PagerClip.entitlements`. | ✅ |
| **صحة ملف Apple AASA** | افتح الرابط في المتصفح وتأكد من قراءته مباشرة: `https://pager.rahapp.com/.well-known/apple-app-site-association` | ✅ |
| **عدم طلب تسجيل دخول (No Auth)** | الـ App Clip يجب أن يفتح صفحة البيجر فورياً بدون طلب بريد أو كلمة مرور للمستخدم العادي (مطابق للباك إند). | ✅ |
| **ربط الـ Widget مع كلا الـ Targets** | ملف `OrderLiveActivity.swift` يجب أن يكون مفعل الخيار لـ `Runner` و `PagerClip`. | ✅ |
| **تضمين الـ Clip داخل الـ App** | التأكد من وجود مرحلة `Embed App Clips` في الـ `Build Phases` للهدف الرئيسي. | ✅ |

---

🚀 **الآن التطبيق والباك إند متوافقان بنسبة 100% مع متطلبات متجر آبل ونظامنا السحابي SaaS!**
