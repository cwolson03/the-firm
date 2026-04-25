'use client'

import { useState } from 'react'
import TabNav from '@/components/TabNav'
import OverviewTab from '@/components/tabs/OverviewTab'
import EconomicsTab from '@/components/tabs/EconomicsTab'
import WeatherTab from '@/components/tabs/WeatherTab'
import SportsTab from '@/components/tabs/SportsTab'
import IntelligenceTab from '@/components/tabs/IntelligenceTab'
import PortfolioTab from '@/components/tabs/PortfolioTab'
import SystemTab from '@/components/tabs/SystemTab'

const TABS = ['Overview', 'Economics', 'Weather', 'Sports', 'Intelligence', 'Portfolio', 'System']

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('Overview')

  return (
    <div className="min-h-screen" style={{ background: '#0a0a0a' }}>
      <TabNav tabs={TABS} active={activeTab} onChange={setActiveTab} />
      <main className="px-6 py-6 max-w-[1600px] mx-auto">
        {activeTab === 'Overview' && <OverviewTab />}
        {activeTab === 'Economics' && <EconomicsTab />}
        {activeTab === 'Weather' && <WeatherTab />}
        {activeTab === 'Sports' && <SportsTab />}
        {activeTab === 'Intelligence' && <IntelligenceTab />}
        {activeTab === 'Portfolio' && <PortfolioTab />}
        {activeTab === 'System' && <SystemTab />}
      </main>
    </div>
  )
}
