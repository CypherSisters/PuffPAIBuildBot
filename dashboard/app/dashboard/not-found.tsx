import Link from "next/link";
import MessagePanel, { proseStyle } from "@/app/_components/MessagePanel";

export default function DashboardNotFound() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center p-6">
      <MessagePanel
        eyebrow="Not found"
        title="There is no page here"
        className="w-full max-w-md"
      >
        <p style={proseStyle}>
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
        </p>
        <p style={proseStyle}>
          <Link href="/dashboard" className="underline">
            Back to the dashboard
          </Link>
        </p>
      </MessagePanel>
    </div>
  );
}
