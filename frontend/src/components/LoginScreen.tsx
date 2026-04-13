import { Building2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

interface LoginScreenProps {
  isSubmitting: boolean;
  error: string | null;
  isLikelyFeishuClient: boolean;
  supportsClientLogin: boolean;
  onSubmit: () => Promise<void>;
}

export default function LoginScreen({
  isSubmitting,
  error,
  isLikelyFeishuClient,
  supportsClientLogin,
  onSubmit,
}: LoginScreenProps) {
  const waitingForFeishuBridge = isLikelyFeishuClient && !supportsClientLogin;

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#f7f3eb] px-6 py-10 text-[#1f1c18]">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(210,176,122,0.26),transparent_32%),radial-gradient(circle_at_bottom_right,rgba(31,28,24,0.08),transparent_28%)]" />
      <div className="relative w-full max-w-5xl overflow-hidden rounded-[32px] border border-[#ddd4c7] bg-[rgba(255,252,246,0.92)] shadow-[0_24px_100px_rgba(44,33,16,0.12)] backdrop-blur-xl lg:grid lg:grid-cols-[1.1fr_0.9fr]">
        <div className="border-b border-[#e7decf] px-7 py-8 lg:border-b-0 lg:border-r lg:px-10 lg:py-12">
          <p className="text-sm uppercase tracking-[0.28em] text-[#8a7f71]">
            KARL Fashion Feed
          </p>
          <h1 className="mt-5 font-display text-5xl leading-[0.96] text-[#2b241d] md:text-6xl">
            飞书组织登录入口
          </h1>
          <p className="mt-6 max-w-xl text-base leading-8 text-[#61584d]">
            正常用户统一通过飞书身份进入系统。首次登录会自动创建你的本地用户档案，后续聊天、记忆和会话都继续挂在同一个内部用户上。
          </p>
        </div>

        <div className="px-7 py-8 lg:px-10 lg:py-12">
          <div className="mx-auto w-full max-w-md">
            <div className="rounded-[28px] border border-[#e4dccf] bg-white/90 p-6 shadow-[0_18px_48px_rgba(44,33,16,0.08)]">
              <p className="text-sm font-medium uppercase tracking-[0.22em] text-[#8a7f71]">
                Feishu Org Auth
              </p>
              <h2 className="mt-3 font-display text-3xl text-[#2b241d]">使用飞书组织账号登录</h2>
              <div className="mt-6 rounded-3xl border border-[#ece3d5] bg-[#fbf8f2] p-5 text-sm leading-7 text-[#6b6358]">
                <div className="flex items-center gap-3 text-[#8b7342]">
                  {supportsClientLogin ? (
                    <Sparkles className="h-5 w-5" />
                  ) : (
                    <Building2 className="h-5 w-5" />
                  )}
                  <span className="font-medium text-[#554c42]">
                    {waitingForFeishuBridge
                      ? "检测到飞书客户端环境，正在等待免登录能力注入。"
                      : supportsClientLogin
                      ? "检测到飞书客户端环境，页面会自动发起免登录；如果失败可点击按钮重试。"
                      : "当前是普通浏览器环境，将跳转到飞书授权页完成登录。"}
                  </span>
                </div>
              </div>

              {error && (
                <div className="mt-4 rounded-2xl border border-[#e8c7c0] bg-[#fff3f0] px-4 py-3 text-sm text-[#8d4a3d]">
                  {error}
                </div>
              )}

              <Button
                onClick={() => void onSubmit()}
                disabled={isSubmitting || waitingForFeishuBridge}
                className="mt-6 h-11 w-full rounded-2xl bg-[#1f1c18] text-[#f7f3eb] hover:bg-[#2c2721]"
              >
                {waitingForFeishuBridge
                  ? "等待飞书客户端免登录..."
                  : isSubmitting
                  ? "登录中..."
                  : supportsClientLogin
                    ? "通过飞书客户端登录"
                    : "跳转飞书授权登录"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
