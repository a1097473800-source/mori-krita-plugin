#import "MoriMac.mm"
#include <cstdio>

static bool connected = false;
static bool failed = false;
static void state(const char *kind, const char *message) {
    printf("%s: %s\n", kind, message);
    if (!strcmp(kind, "ready")) connected = true;
    if (!strcmp(kind, "error")) failed = true;
}
int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 2) return 2;
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        NSWindow *window = [[NSWindow alloc] initWithContentRect:NSMakeRect(100, 100, 800, 600)
            styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable backing:NSBackingStoreBuffered defer:NO];
        [window makeKeyAndOrderFront:nil];
        void *handle = mori_web_create((__bridge void *)window.contentView, argv[1], state);
        if (!handle) return 3;
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:40];
        while (!connected && !failed && deadline.timeIntervalSinceNow > 0)
            [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        if (!connected) { mori_web_close(handle); return 4; }
        MoriWebHost *host = (__bridge MoriWebHost *)handle;
        __block BOOL scriptDone = NO, contentValid = NO;
        [host.view evaluateJavaScript:@"document.body.dataset.moriSmoke === 'ready'" completionHandler:^(id value, NSError *error) {
            contentValid = !error && [value boolValue]; scriptDone = YES;
        }];
        deadline = [NSDate dateWithTimeIntervalSinceNow:10];
        while (!scriptDone && deadline.timeIntervalSinceNow > 0)
            [[NSRunLoop mainRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
        mori_web_visible(handle, 0);
        if (!host.view.hidden) return 5;
        mori_web_visible(handle, 1);
        mori_web_resize(handle);
        mori_web_focus(handle);
        mori_web_close(handle);
        if (!scriptDone || !contentValid) return 6;
        puts("MORI_MAC_NATIVE_SMOKE_PASSED");
        return 0;
    }
}
