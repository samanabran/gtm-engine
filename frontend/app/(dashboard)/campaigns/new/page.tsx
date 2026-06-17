"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { submitJson } from "@/lib/api";
import { Breadcrumbs } from "@/components/layout/breadcrumbs";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type CampaignPayload = {
  name: string;
  tone: string;
  product_value_prop?: string;
  brand_voice?: string;
  target_icp?: Record<string, unknown>;
};

export default function NewCampaignPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [tone, setTone] = useState("professional");
  const [productValueProp, setProductValueProp] = useState("");
  const [brandVoice, setBrandVoice] = useState("");
  const [icpThreshold, setIcpThreshold] = useState("");
  const [nameError, setNameError] = useState("");

  const createMutation = useMutation({
    mutationFn: async (payload: CampaignPayload) => {
      const result = await submitJson<{ id: string }>("/campaigns", payload);
      if (!result.ok) throw new Error("Failed to create campaign");
      return result.data;
    },
    onSuccess: (data) => {
      toast.success("Campaign created successfully");
      router.push(`/campaigns/${data?.id}`);
    },
    onError: () => {
      toast.error("Failed to create campaign. Please try again.");
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      setNameError("Campaign name is required");
      return;
    }
    setNameError("");
    const payload: CampaignPayload = {
      name: name.trim(),
      tone,
      ...(productValueProp.trim() && { product_value_prop: productValueProp.trim() }),
      ...(brandVoice.trim() && { brand_voice: brandVoice.trim() }),
      ...(icpThreshold && { target_icp: { threshold: parseFloat(icpThreshold) } }),
    };
    createMutation.mutate(payload);
  }

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Dashboard", href: "/" },
          { label: "Campaigns", href: "/campaigns" },
          { label: "New" },
        ]}
      />
      <Card>
        <CardContent className="space-y-5 p-5">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Campaign wizard</p>
            <h1 className="font-display text-3xl font-semibold">Create a review-ready outbound motion</h1>
          </div>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-1">
                <label htmlFor="name" className="text-sm font-medium">
                  Campaign Name <span className="text-red-500">*</span>
                </label>
                <Input
                  id="name"
                  placeholder="e.g. Founder-led Expansion"
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (nameError) setNameError("");
                  }}
                />
                {nameError && <p className="text-xs text-red-500">{nameError}</p>}
              </div>
              <div className="space-y-1">
                <label htmlFor="tone" className="text-sm font-medium">Tone</label>
                <select
                  id="tone"
                  value={tone}
                  onChange={(e) => setTone(e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                  <option value="professional">Professional</option>
                  <option value="casual">Casual</option>
                  <option value="formal">Formal</option>
                  <option value="consultative">Consultative</option>
                </select>
              </div>
              <div className="space-y-1">
                <label htmlFor="icp-threshold" className="text-sm font-medium">ICP Score Threshold</label>
                <Input
                  id="icp-threshold"
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  placeholder="e.g. 0.7"
                  value={icpThreshold}
                  onChange={(e) => setIcpThreshold(e.target.value)}
                />
              </div>
            </div>
            <div className="space-y-1">
              <label htmlFor="value-prop" className="text-sm font-medium">Product Value Proposition</label>
              <Textarea
                id="value-prop"
                placeholder="e.g. Shorten the time from signal to meeting."
                value={productValueProp}
                onChange={(e) => setProductValueProp(e.target.value)}
                rows={3}
              />
            </div>
            <div className="space-y-1">
              <label htmlFor="brand-voice" className="text-sm font-medium">Brand Voice</label>
              <Textarea
                id="brand-voice"
                placeholder="e.g. Use high-intent leads only. Draft three variants and require approval before send."
                value={brandVoice}
                onChange={(e) => setBrandVoice(e.target.value)}
                rows={3}
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={createMutation.isPending}>
                {createMutation.isPending ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Creating…
                  </>
                ) : (
                  "Create campaign"
                )}
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => router.push("/campaigns")}
                disabled={createMutation.isPending}
              >
                Cancel
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
