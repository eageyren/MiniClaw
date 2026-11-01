import type { Metadata } from "next";
import "./style.css";

export const metadata: Metadata = { title: "MiniClaw · 陪你聊聊天", description: "MiniClaw 家庭陪伴与提醒", manifest: "/manifest.json", appleWebApp: { capable: true, title: "MiniClaw", statusBarStyle: "default" } };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="zh-CN"><body>{children}</body></html>; }
