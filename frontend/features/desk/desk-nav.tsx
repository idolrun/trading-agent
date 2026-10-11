"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Activity,
  Bell,
  Briefcase,
  ChartNoAxesCombined,
  ClipboardList,
  Inbox,
  MessageSquare,
  PenLine,
  Rss,
  ScrollText,
  Settings,
  Tags,
  Users,
  type LucideIcon,
} from "lucide-react"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
  useSidebar,
} from "@/components/ui/sidebar"

const LINKS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/", label: "Inbox", icon: Inbox },
  { href: "/feed", label: "Feed", icon: Rss },
  { href: "/positions", label: "Positions", icon: Briefcase },
  { href: "/orders", label: "Orders", icon: ClipboardList },
  { href: "/reports/trading", label: "Reports", icon: ChartNoAxesCombined },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/health", label: "Health", icon: Activity },
  { href: "/manual/entry", label: "Manual entry", icon: PenLine },
  { href: "/discord", label: "Discord", icon: MessageSquare },
  { href: "/config", label: "Config", icon: Settings },
  { href: "/users", label: "Users", icon: Users },
  { href: "/audit", label: "Audit", icon: ScrollText },
  { href: "/labels", label: "Labels", icon: Tags },
]

export function DeskNav() {
  const pathname = usePathname()
  const { setOpenMobile } = useSidebar()
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="group-data-[collapsible=icon]:hidden">
        <span className="px-2 py-1.5 font-heading text-sm font-semibold">Desk</span>
      </SidebarHeader>
      <SidebarSeparator className="group-data-[collapsible=icon]:hidden" />
      <SidebarContent>
        <nav aria-label="Desk">
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {LINKS.map(({ href, label, icon: Icon }) => {
                  const active = href === "/" ? pathname === "/" : pathname.startsWith(href)
                  return (
                    <SidebarMenuItem key={href}>
                      <SidebarMenuButton asChild isActive={active} tooltip={label}>
                        <Link href={href} aria-current={active ? "page" : undefined} onClick={() => setOpenMobile(false)}>
                          <Icon />
                          <span>{label}</span>
                        </Link>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </nav>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
