#import <AppKit/AppKit.h>
#include <cstdio>

static NSString *RunInstall(NSString *resources, NSString *testRoot, void (^progress)(NSString *), NSError **error) {
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    NSMutableArray *arguments = [NSMutableArray arrayWithArray:@[[resources stringByAppendingPathComponent:@"install.sh"], resources]];
    if (testRoot) [arguments addObject:testRoot];
    task.arguments = arguments;
    NSPipe *pipe = [NSPipe pipe];
    task.standardOutput = pipe; task.standardError = pipe;
    if (![task launchAndReturnError:error]) return nil;
    NSMutableData *all = [NSMutableData data];
    while (true) {
        NSData *data = [pipe.fileHandleForReading availableData];
        if (!data.length) break;
        [all appendData:data];
        if (progress) progress([[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"");
    }
    [task waitUntilExit];
    NSString *output = [[NSString alloc] initWithData:all encoding:NSUTF8StringEncoding] ?: @"";
    if (task.terminationStatus != 0) {
        NSString *message = @"安装未完成，请重新下载完整安装包后重试。";
        for (NSString *line in [output componentsSeparatedByString:@"\n"])
            if ([line hasPrefix:@"MORI_ERROR|"]) message = [line substringFromIndex:11];
        if (error) *error = [NSError errorWithDomain:@"MoriInstaller" code:task.terminationStatus
            userInfo:@{NSLocalizedDescriptionKey:message, @"InstallerLog":output}];
        return nil;
    }
    for (NSString *line in [output componentsSeparatedByString:@"\n"])
        if ([line hasPrefix:@"MORI_RESULT|"]) return [line substringFromIndex:12];
    return nil;
}

@interface MoriInstallerCanvas : NSView
@end
@implementation MoriInstallerCanvas
- (void)drawRect:(NSRect)dirtyRect {
    [[NSColor colorWithRed:.13 green:.13 blue:.15 alpha:1] setFill];
    NSRectFill(dirtyRect);
    [[NSColor colorWithWhite:.23 alpha:1] setStroke];
    NSBezierPath *line = [NSBezierPath bezierPath];
    [line moveToPoint:NSMakePoint(36, 80)]; [line lineToPoint:NSMakePoint(584, 80)];
    line.lineWidth = 1; [line stroke];
}
@end

@interface MoriInstallButton : NSButton
@end
@implementation MoriInstallButton
- (void)drawRect:(NSRect)dirtyRect {
    NSColor *background = self.enabled ?
        [NSColor colorWithRed:(self.highlighted ? .66 : .72) green:(self.highlighted ? .58 : .64) blue:.91 alpha:1] :
        [NSColor colorWithWhite:.34 alpha:1];
    [background setFill];
    [[NSBezierPath bezierPathWithRoundedRect:NSInsetRect(self.bounds, 1, 1) xRadius:8 yRadius:8] fill];
    NSMutableParagraphStyle *paragraph = [NSMutableParagraphStyle new];
    paragraph.alignment = NSTextAlignmentCenter;
    NSDictionary *attributes = @{NSFontAttributeName:[NSFont systemFontOfSize:13 weight:NSFontWeightSemibold],
        NSForegroundColorAttributeName:[NSColor colorWithRed:.15 green:.12 blue:.20 alpha:1],
        NSParagraphStyleAttributeName:paragraph};
    CGFloat height = [self.title sizeWithAttributes:attributes].height;
    [self.title drawInRect:NSMakeRect(0, (self.bounds.size.height - height) / 2, self.bounds.size.width, height)
        withAttributes:attributes];
}
@end

@interface MoriInstaller : NSObject <NSApplicationDelegate>
@property(strong) NSWindow *window;
@property(strong) NSTextField *status;
@property(strong) NSButton *button;
@property(strong) NSProgressIndicator *progress;
@property(strong) NSString *installedApp;
@property(assign) BOOL busy;
@end

@implementation MoriInstaller
- (NSTextField *)text:(NSString *)value frame:(NSRect)frame size:(CGFloat)size color:(NSColor *)color {
    NSTextField *label = [NSTextField labelWithString:value];
    label.frame = frame; label.font = [NSFont systemFontOfSize:size]; label.textColor = color;
    label.lineBreakMode = NSLineBreakByWordWrapping; label.maximumNumberOfLines = 0;
    [self.window.contentView addSubview:label]; return label;
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 620, 440)
        styleMask:NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
        backing:NSBackingStoreBuffered defer:NO];
    self.window.title = @"Mori for Krita · Mac 安装";
    self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
    self.window.backgroundColor = [NSColor colorWithRed:.13 green:.13 blue:.15 alpha:1];
    self.window.contentView = [[MoriInstallerCanvas alloc] initWithFrame:NSMakeRect(0, 0, 620, 440)];
    [self.window center];
    [self text:@"MORI" frame:NSMakeRect(36, 366, 500, 35) size:25 color:NSColor.whiteColor];
    [self text:@"Mac 测试版" frame:NSMakeRect(492, 373, 100, 20) size:11
        color:[NSColor colorWithRed:.72 green:.64 blue:.91 alpha:1]];
    [self text:@"让创作在 Mac 上继续" frame:NSMakeRect(36, 309, 540, 32) size:23 color:NSColor.whiteColor];
    [self text:@"Krita 5.3.4.1  ·  Mori __MORI_VERSION__  ·  AI Diffusion 1.49.0"
        frame:NSMakeRect(36, 269, 550, 27) size:13 color:[NSColor colorWithWhite:.70 alpha:1]];
    [self text:@"实时生成、选区助手与工作台\n保留 Mori 深灰与淡紫界面，默认使用原图无损参考。"
        frame:NSMakeRect(36, 179, 548, 65) size:14 color:[NSColor colorWithWhite:.83 alpha:1]];
    self.status = [self text:@"安装前请保存作品并完全退出 Krita。"
        frame:NSMakeRect(36, 109, 548, 46) size:12 color:[NSColor colorWithWhite:.65 alpha:1]];
    self.progress = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(36, 91, 548, 5)];
    self.progress.style = NSProgressIndicatorStyleBar; self.progress.indeterminate = YES;
    self.progress.hidden = YES; [self.window.contentView addSubview:self.progress];
    [self text:@"Apple 芯片与 Intel  ·  macOS 12 或更新版本"
        frame:NSMakeRect(36, 37, 370, 21) size:11 color:[NSColor colorWithWhite:.5 alpha:1]];
    self.button = [[MoriInstallButton alloc] initWithFrame:NSMakeRect(462, 28, 122, 39)];
    self.button.title = @"安装"; self.button.target = self; self.button.action = @selector(install:);
    self.button.bordered = NO;
    self.button.keyEquivalent = @"\r";
    [self.window.contentView addSubview:self.button];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
- (void)install:(id)sender {
    if (self.installedApp) {
        [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:self.installedApp]];
        [NSApp terminate:nil]; return;
    }
    if (self.busy) return;
    self.busy = YES; self.button.enabled = NO;
    [self.window standardWindowButton:NSWindowCloseButton].enabled = NO;
    self.progress.hidden = NO; [self.progress startAnimation:nil];
    NSString *resources = NSBundle.mainBundle.resourcePath;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSError *error = nil;
        NSString *result = RunInstall(resources, nil, ^(NSString *chunk) {
            dispatch_async(dispatch_get_main_queue(), ^{
                for (NSString *line in [chunk componentsSeparatedByString:@"\n"]) {
                    NSArray *parts = [line componentsSeparatedByString:@"|"];
                    if (parts.count == 3 && [parts[0] isEqualToString:@"MORI_PROGRESS"]) self.status.stringValue = parts[2];
                }
            });
        }, &error);
        dispatch_async(dispatch_get_main_queue(), ^{
            self.busy = NO; self.button.enabled = YES;
            [self.window standardWindowButton:NSWindowCloseButton].enabled = YES;
            [self.progress stopAnimation:nil]; self.progress.hidden = YES;
            if (result) {
                self.installedApp = result; self.status.stringValue = @"安装完成。点击打开 Krita，即可开始使用 Mori。";
                self.button.title = @"打开 Krita";
            } else {
                self.status.stringValue = error.localizedDescription ?: @"安装未完成，请重试。";
                self.button.title = @"重试";
            }
        });
    });
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    return self.busy ? NSTerminateCancel : NSTerminateNow;
}
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return !self.busy; }
@end

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc == 3 && !strcmp(argv[1], "--self-test")) {
            NSError *error = nil;
            NSString *result = RunInstall(NSBundle.mainBundle.resourcePath,
                [NSString stringWithUTF8String:argv[2]], nil, &error);
            if (!result) { fprintf(stderr, "%s\n", error.description.UTF8String); return 1; }
            printf("MORI_INSTALL_TEST_PASSED|%s\n", result.UTF8String); return 0;
        }
        [NSApplication sharedApplication];
        MoriInstaller *delegate = [MoriInstaller new]; NSApp.delegate = delegate;
        if (argc == 3 && !strcmp(argv[1], "--render-preview")) {
            [delegate applicationDidFinishLaunching:nil];
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.2]];
            [delegate.window displayIfNeeded];
            NSView *view = delegate.window.contentView;
            NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL
                pixelsWide:1240 pixelsHigh:880 bitsPerSample:8 samplesPerPixel:4 hasAlpha:YES
                isPlanar:NO colorSpaceName:NSCalibratedRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
            bitmap.size = view.bounds.size;
            [view cacheDisplayInRect:view.bounds toBitmapImageRep:bitmap];
            NSData *data = [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
            return [data writeToFile:[NSString stringWithUTF8String:argv[2]] atomically:YES] ? 0 : 2;
        }
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular]; [NSApp run];
    }
    return 0;
}
