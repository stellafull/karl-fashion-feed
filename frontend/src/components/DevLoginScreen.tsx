import { useState } from "react";
import { LockKeyhole, Shield, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface DevLoginScreenProps {
  isSubmitting: boolean;
  error: string | null;
  onSubmit: (loginName: string, password: string) => Promise<void>;
}

export default function DevLoginScreen({
  isSubmitting,
  error,
  onSubmit,
}: DevLoginScreenProps) {
  const [loginName, setLoginName] = useState("dev-root");
  const [password, setPassword] = useState("");

  const handleSubmit = async () => {
    await onSubmit(loginName, password);
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#121212] px-6 py-10 text-white">
      <div className="relative w-full max-w-md rounded-[28px] border border-white/10 bg-[#1d1d1d] p-8 shadow-[0_24px_100px_rgba(0,0,0,0.45)]">
        <div className="flex items-center gap-3 text-[#c7b07b]">
          <Shield className="h-5 w-5" />
          <p className="text-sm uppercase tracking-[0.24em]">Dev Only</p>
        </div>
        <h1 className="mt-4 text-3xl font-semibold text-white">dev-root 调试登录</h1>
        <p className="mt-3 text-sm leading-7 text-white/70">
          这个入口只保留给内部调试，不属于正常用户登录路径。
        </p>

        <div className="mt-6 space-y-4">
          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-sm text-white/75">
              <UserRound className="h-4 w-4" />
              Login name
            </span>
            <Input
              value={loginName}
              onChange={(event) => setLoginName(event.target.value)}
              placeholder="dev-root"
              className="h-11 rounded-2xl border-white/10 bg-white/5 px-4 text-white shadow-none"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void handleSubmit();
                }
              }}
            />
          </label>

          <label className="block">
            <span className="mb-2 flex items-center gap-2 text-sm text-white/75">
              <LockKeyhole className="h-4 w-4" />
              Password
            </span>
            <Input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
              className="h-11 rounded-2xl border-white/10 bg-white/5 px-4 text-white shadow-none"
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
          className="mt-6 h-11 w-full rounded-2xl bg-[#c7b07b] text-black hover:bg-[#d1bc8d]"
        >
          {isSubmitting ? "登录中..." : "使用 dev-root 登录"}
        </Button>
      </div>
    </div>
  );
}
