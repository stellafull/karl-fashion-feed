import { useState } from "react";
import { LockKeyhole, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface LoginScreenProps {
  isSubmitting: boolean;
  error: string | null;
  onSubmit: (loginName: string, password: string) => Promise<void>;
}

export default function LoginScreen({
  isSubmitting,
  error,
  onSubmit,
}: LoginScreenProps) {
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = async () => {
    await onSubmit(loginName, password);
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#f7f3eb] px-6 py-10 text-[#1f1c18]">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(210,176,122,0.26),transparent_32%),radial-gradient(circle_at_bottom_right,rgba(31,28,24,0.08),transparent_28%)]" />
      <div className="relative w-full max-w-5xl overflow-hidden rounded-[32px] border border-[#ddd4c7] bg-[rgba(255,252,246,0.92)] shadow-[0_24px_100px_rgba(44,33,16,0.12)] backdrop-blur-xl lg:grid lg:grid-cols-[1.1fr_0.9fr]">
        <div className="border-b border-[#e7decf] px-7 py-8 lg:border-b-0 lg:border-r lg:px-10 lg:py-12">
          <p className="text-sm uppercase tracking-[0.28em] text-[#8a7f71]">
            KARL Fashion Feed
          </p>
          <h1 className="mt-5 font-display text-5xl leading-[0.96] text-[#2b241d] md:text-6xl">
            Frontend / Backend 联调入口
          </h1>
          <p className="mt-6 max-w-xl text-base leading-8 text-[#61584d]">
            当前聊天、鉴权和持久化会话都直接走 FastAPI 后端。登录后即可验证 JWT、
            session 列表、消息创建和 assistant 轮询链路。
          </p>
        </div>

        <div className="px-7 py-8 lg:px-10 lg:py-12">
          <div className="mx-auto w-full max-w-md">
            <div className="rounded-[28px] border border-[#e4dccf] bg-white/90 p-6 shadow-[0_18px_48px_rgba(44,33,16,0.08)]">
              <p className="text-sm font-medium uppercase tracking-[0.22em] text-[#8a7f71]">
                Local Auth
              </p>
              <h2 className="mt-3 font-display text-3xl text-[#2b241d]">使用本地账号登录</h2>

              <div className="mt-6 space-y-4">
                <label className="block">
                  <span className="mb-2 flex items-center gap-2 text-sm text-[#6a6156]">
                    <UserRound className="h-4 w-4" />
                    Login name
                  </span>
                  <Input
                    value={loginName}
                    onChange={(event) => setLoginName(event.target.value)}
                    placeholder="root"
                    className="h-11 rounded-2xl border-[#ddd4c7] bg-[#fcfaf6] px-4 shadow-none"
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void handleSubmit();
                      }
                    }}
                  />
                </label>

                <label className="block">
                  <span className="mb-2 flex items-center gap-2 text-sm text-[#6a6156]">
                    <LockKeyhole className="h-4 w-4" />
                    Password
                  </span>
                  <Input
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="••••••••"
                    className="h-11 rounded-2xl border-[#ddd4c7] bg-[#fcfaf6] px-4 shadow-none"
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        void handleSubmit();
                      }
                    }}
                  />
                </label>
              </div>

              {error && (
                <div className="mt-4 rounded-2xl border border-[#e8c7c0] bg-[#fff3f0] px-4 py-3 text-sm text-[#8d4a3d]">
                  {error}
                </div>
              )}

              <Button
                onClick={() => void handleSubmit()}
                disabled={isSubmitting}
                className="mt-6 h-11 w-full rounded-2xl bg-[#1f1c18] text-[#f7f3eb] hover:bg-[#2c2721]"
              >
                {isSubmitting ? "登录中..." : "登录并开始联调"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
