"use client"

import { Fragment } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import {
  Activity,
  Bell,
  Briefcase,
  ChartNoAxesCombined,
  ClipboardList,
  Gauge,
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
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  SidebarSeparator,
  useSidebar,
} from "@/components/ui/sidebar"

type DeskLink = { href: string; label: string; icon: LucideIcon }

/** Primary book, then monitoring, then administration. Dividers mark the boundary. */
const GROUPS: { id: string; label: string; links: DeskLink[] }[] = [
  {
    id: "trading",
    label: "Trading",
    links: [
      { href: "/", label: "Inbox", icon: Inbox },
      { href: "/control-room", label: "Control room", icon: Gauge },
      { href: "/feed", label: "Feed", icon: Rss },
      { href: "/positions", label: "Positions", icon: Briefcase },
      { href: "/orders", label: "Orders", icon: ClipboardList },
      { href: "/reports/trading", label: "Reports", icon: ChartNoAxesCombined },
    ],
  },
  {
    id: "operations",
    label: "Operations",
    links: [
      { href: "/alerts", label: "Alerts", icon: Bell },
      { href: "/health", label: "Health", icon: Activity },
      { href: "/manual/entry", label: "Manual entry", icon: PenLine },
      { href: "/discord", label: "Discord", icon: MessageSquare },
    ],
  },
  {
    id: "admin",
    label: "Admin",
    links: [
      { href: "/config", label: "Config", icon: Settings },
      { href: "/users", label: "Users", icon: Users },
      { href: "/audit", label: "Audit", icon: ScrollText },
      { href: "/labels", label: "Labels", icon: Tags },
    ],
  },
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
          {GROUPS.map((group, index) => (
            <Fragment key={group.id}>
              {index > 0 ? <SidebarSeparator /> : null}
              <SidebarGroup>
                <SidebarGroupLabel id={`desk-nav-${group.id}`}>{group.label}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <SidebarMenu aria-labelledby={`desk-nav-${group.id}`}>
                    {group.links.map(({ href, label, icon: Icon }) => {
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
            </Fragment>
          ))}
        </nav>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  )
}
