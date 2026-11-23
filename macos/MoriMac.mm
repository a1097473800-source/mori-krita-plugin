// Native macOS integration. Loaded into Krita's GUI process; no Python extras.
#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#import <Security/Security.h>
#include <cstdlib>
#include <cstring>

typedef void (*MoriCallback)(const char *, const char *);
static NSString *const MoriKeychainService = @"chat.morinet.krita.session";

static BOOL MoriSameOrigin(NSURL *url, NSURL *origin) {
    return url && [url.scheme isEqualToString:origin.scheme] &&
        [url.host isEqualToString:origin.host] && [url.port isEqual:origin.port] &&
        !url.user && !url.password;
}

static BOOL MoriLocalURL(NSURL *url) {
    return [url.scheme isEqualToString:@"http"] && [url.host isEqualToString:@"127.0.0.1"] &&
        url.port.integerValue > 0 && url.port.integerValue <= 65535 && !url.user && !url.password;
}

@interface MoriWebHost : NSObject <WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate>
@property(nonatomic, strong) WKWebView *view;
@property(nonatomic, strong) NSURL *origin;
@property(nonatomic, assign) MoriCallback callback;
@property(nonatomic, assign) BOOL closed;
@property(nonatomic, strong) NSMutableSet<WKDownload *> *downloads;
- (void)emit:(NSString *)kind message:(NSString *)message;
- (void)close;
@end

@implementation MoriWebHost
- (void)emit:(NSString *)kind message:(NSString *)message {
    if (!self.closed && self.callback) self.callback(kind.UTF8String, message.UTF8String);
}
- (void)close {
    if (self.closed) return;
    self.closed = YES;
    self.callback = nullptr;
    for (WKDownload *download in self.downloads.copy) {
        download.delegate = nil;
        [download cancel:^(NSData *resumeData) { }];
    }
    [self.downloads removeAllObjects];
    [self.view stopLoading];
    self.view.navigationDelegate = nil;
    self.view.UIDelegate = nil;
    [self.view removeFromSuperview];
    self.view = nil;
}
- (void)webView:(WKWebView *)view didFinishNavigation:(WKNavigation *)navigation {
    if (MoriSameOrigin(view.URL, self.origin)) [self emit:@"ready" message:@"Mori 画布已连接"];
}
- (void)webView:(WKWebView *)view didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (error.code != NSURLErrorCancelled) [self emit:@"error" message:@"画布连接失败，请检查本机服务后重试。"];
}
- (void)webView:(WKWebView *)view didFailNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (error.code != NSURLErrorCancelled) [self emit:@"error" message:@"画布加载未完成，请重新连接。"];
}
- (void)webViewWebContentProcessDidTerminate:(WKWebView *)view {
    [self emit:@"error" message:@"画布引擎已退出，请重新打开工作台。"];
}
- (void)webView:(WKWebView *)view decidePolicyForNavigationAction:(WKNavigationAction *)action
        decisionHandler:(void (^)(WKNavigationActionPolicy))decision {
    if (self.closed) { decision(WKNavigationActionPolicyCancel); return; }
    NSURL *url = action.request.URL;
    if (MoriSameOrigin(url, self.origin)) {
        if (action.shouldPerformDownload) decision(WKNavigationActionPolicyDownload);
        else decision(WKNavigationActionPolicyAllow);
        return;
    }
    // Blob exports belong to the current loopback origin. WebKit owns the bytes.
    NSString *prefix = [@"blob:" stringByAppendingString:self.origin.absoluteString];
    if (action.shouldPerformDownload && [url.absoluteString hasPrefix:prefix]) {
        decision(WKNavigationActionPolicyDownload);
        return;
    }
    if (action.navigationType == WKNavigationTypeLinkActivated &&
            [url.scheme isEqualToString:@"https"] && !url.user && !url.password) {
        [[NSWorkspace sharedWorkspace] openURL:url];
    }
    decision(WKNavigationActionPolicyCancel);
}
- (void)webView:(WKWebView *)view decidePolicyForNavigationResponse:(WKNavigationResponse *)response
        decisionHandler:(void (^)(WKNavigationResponsePolicy))decision {
    if (self.closed) { decision(WKNavigationResponsePolicyCancel); return; }
    decision(response.canShowMIMEType ? WKNavigationResponsePolicyAllow : WKNavigationResponsePolicyDownload);
}
- (WKWebView *)webView:(WKWebView *)view createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration
        forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
    NSURL *url = action.request.URL;
    if (!self.closed && action.navigationType == WKNavigationTypeLinkActivated &&
            [url.scheme isEqualToString:@"https"] && !url.user && !url.password) {
        [[NSWorkspace sharedWorkspace] openURL:url];
    }
    return nil;
}
- (void)webView:(WKWebView *)view runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters
        initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))completion {
    if (self.closed || !MoriSameOrigin(frame.request.URL, self.origin)) { completion(nil); return; }
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.title = @"选择参考图片";
    panel.canChooseDirectories = NO;
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    [panel beginSheetModalForWindow:view.window completionHandler:^(NSModalResponse response) {
        completion(!self.closed && response == NSModalResponseOK ? panel.URLs : nil);
    }];
}
- (void)webView:(WKWebView *)view requestMediaCapturePermissionForOrigin:(WKSecurityOrigin *)origin
        initiatedByFrame:(WKFrameInfo *)frame type:(WKMediaCaptureType)type
        decisionHandler:(void (^)(WKPermissionDecision))decision API_AVAILABLE(macos(12.0)) {
    decision(WKPermissionDecisionDeny);
}
- (void)webView:(WKWebView *)view navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download {
    if (self.closed) { [download cancel:^(NSData *resumeData) { }]; return; }
    [self.downloads addObject:download]; download.delegate = self;
}
- (void)webView:(WKWebView *)view navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download {
    if (self.closed) { [download cancel:^(NSData *resumeData) { }]; return; }
    [self.downloads addObject:download]; download.delegate = self;
}
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response
        suggestedFilename:(NSString *)suggested completionHandler:(void (^)(NSURL *))completion {
    NSString *name = suggested.lastPathComponent;
    NSSet *allowed = [NSSet setWithArray:@[@"png", @"jpg", @"jpeg", @"webp", @"svg", @"psd", @"zip", @"json"]];
    if (self.closed || response.expectedContentLength > 250 * 1024 * 1024 ||
            ![allowed containsObject:name.pathExtension.lowercaseString]) {
        completion(nil); return;
    }
    NSSavePanel *panel = [NSSavePanel savePanel];
    panel.title = @"保存 Mori 导出文件";
    panel.nameFieldStringValue = name;
    panel.canCreateDirectories = YES;
    [panel beginSheetModalForWindow:self.view.window completionHandler:^(NSModalResponse result) {
        completion(!self.closed && result == NSModalResponseOK ? panel.URL : nil);
    }];
}
- (void)download:(WKDownload *)download willPerformHTTPRedirection:(NSHTTPURLResponse *)response
        newRequest:(NSURLRequest *)request decisionHandler:(void (^)(WKDownloadRedirectPolicy))decision {
    decision(MoriSameOrigin(request.URL, self.origin) ? WKDownloadRedirectPolicyAllow : WKDownloadRedirectPolicyCancel);
}
- (void)downloadDidFinish:(WKDownload *)download { [self.downloads removeObject:download]; }
- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData {
    [self.downloads removeObject:download];
    if (error.code != NSURLErrorCancelled) [self emit:@"notice" message:@"导出未完成，请重新选择保存位置。"];
}
@end

extern "C" {
int mori_mac_abi(void) { return 1; }
void *mori_web_create(void *parent, const char *url, MoriCallback callback) {
    if (![NSThread isMainThread] || !parent || !url) return nullptr;
    @try {
        NSView *container = (__bridge NSView *)parent;
        NSURL *address = [NSURL URLWithString:[NSString stringWithUTF8String:url]];
        if (![container isKindOfClass:[NSView class]] || !MoriLocalURL(address)) return nullptr;
        MoriWebHost *host = [MoriWebHost new];
        host.origin = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%@/", address.port]];
        host.callback = callback;
        host.downloads = [NSMutableSet set];
        WKWebViewConfiguration *config = [WKWebViewConfiguration new];
        config.websiteDataStore = [WKWebsiteDataStore nonPersistentDataStore];
        config.preferences.javaScriptCanOpenWindowsAutomatically = NO;
        // Network subresources are confined to the bridge and image data/blob URLs.
        // The gateway proxies all account/media/API traffic, including remote inspiration.
        NSString *rules = @"[{\"trigger\":{\"url-filter\":\".*\"},\"action\":{\"type\":\"block\"}},"
            "{\"trigger\":{\"url-filter\":\"^http://127\\\\.0\\\\.0\\\\.1:PORT/\"},\"action\":{\"type\":\"ignore-previous-rules\"}},"
            "{\"trigger\":{\"url-filter\":\"^(data:|blob:http://127\\\\.0\\\\.0\\\\.1:PORT/)\"},\"action\":{\"type\":\"ignore-previous-rules\"}}]";
        rules = [rules stringByReplacingOccurrencesOfString:@"PORT" withString:address.port.stringValue];
        host.view = [[WKWebView alloc] initWithFrame:container.bounds configuration:config];
        host.view.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
        host.view.navigationDelegate = host;
        host.view.UIDelegate = host;
        if (@available(macOS 12.0, *)) host.view.underPageBackgroundColor = [NSColor colorWithWhite:0.075 alpha:1];
        [container addSubview:host.view];
        [[WKContentRuleListStore defaultStore] compileContentRuleListForIdentifier:@"MoriLoopback-v1"
            encodedContentRuleList:rules completionHandler:^(WKContentRuleList *list, NSError *error) {
                if (host.closed) return;
                if (error || !list) { [host emit:@"error" message:@"无法初始化画布网络规则。请重新打开工作台。"]; return; }
                [host.view.configuration.userContentController addContentRuleList:list];
                [host.view loadRequest:[NSURLRequest requestWithURL:address]];
            }];
        return (__bridge_retained void *)host;
    } @catch (NSException *exception) { return nullptr; }
}
void mori_web_visible(void *handle, int visible) {
    if (!handle || ![NSThread isMainThread]) return;
    MoriWebHost *host = (__bridge MoriWebHost *)handle;
    if (!host.closed) host.view.hidden = !visible;
}
void mori_web_resize(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    MoriWebHost *host = (__bridge MoriWebHost *)handle;
    if (!host.closed && host.view.superview) host.view.frame = host.view.superview.bounds;
}
void mori_web_focus(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    MoriWebHost *host = (__bridge MoriWebHost *)handle;
    if (!host.closed) [host.view.window makeFirstResponder:host.view];
}
void mori_web_close(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    MoriWebHost *host = (__bridge_transfer MoriWebHost *)handle;
    [host close];
}
static NSMutableDictionary *MoriKeyQuery(const char *account) {
    if (!account) return nil;
    NSString *name = [NSString stringWithUTF8String:account];
    if (!name || ![name hasPrefix:@"https://"] || name.length > 2048) return nil;
    return [@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService:MoriKeychainService, (__bridge id)kSecAttrAccount:name,
        (__bridge id)kSecUseAuthenticationUI:(__bridge id)kSecUseAuthenticationUIFail} mutableCopy];
}
void *mori_keychain_get(const char *account) {
    NSMutableDictionary *query = MoriKeyQuery(account);
    if (!query) return nullptr;
    query[(__bridge id)kSecReturnData] = @YES;
    query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef result = nullptr;
    if (SecItemCopyMatching((__bridge CFDictionaryRef)query, &result) != errSecSuccess || !result) return nullptr;
    NSData *data = CFBridgingRelease(result);
    if (data.length > 65536) return nullptr;
    char *buffer = (char *)calloc(data.length + 1, 1);
    if (buffer) memcpy(buffer, data.bytes, data.length);
    return buffer;
}
int mori_keychain_set(const char *account, const char *bytes) {
    NSMutableDictionary *query = MoriKeyQuery(account);
    if (!query || !bytes || strlen(bytes) > 65536) return 0;
    NSData *data = [[NSString stringWithUTF8String:bytes] dataUsingEncoding:NSUTF8StringEncoding];
    if (!data) return 0;
    OSStatus status = SecItemUpdate((__bridge CFDictionaryRef)query,
        (__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:data});
    if (status == errSecItemNotFound) {
        query[(__bridge id)kSecValueData] = data;
        status = SecItemAdd((__bridge CFDictionaryRef)query, nullptr);
    }
    return status == errSecSuccess;
}
void mori_keychain_delete(const char *account) {
    NSMutableDictionary *query = MoriKeyQuery(account);
    if (query) SecItemDelete((__bridge CFDictionaryRef)query);
}
void mori_mac_free(void *buffer) { free(buffer); }
}
