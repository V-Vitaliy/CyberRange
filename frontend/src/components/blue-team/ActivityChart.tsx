"use client"
import React, { useMemo } from 'react';
import { LineChart, Line, XAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';

export default function ActivityChart({ data }: { data: any[] }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) {
      return [
        { time: '10:00', v: 0 }, { time: '11:00', v: 0 }, { time: '12:00', v: 0 }, { time: '13:00', v: 0 }
      ];
    }

    const counts: Record<string, number> = {};

    data.forEach(log => {
      if (!log.timestamp) return;
      const d = new Date(log.timestamp);
      const timeStr = `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
      counts[timeStr] = (counts[timeStr] || 0) + 1;
    });

    return Object.keys(counts).sort().map(k => ({ time: k, v: counts[k] }));
  }, [data]);

  return (
    <div className="h-32 w-full mt-4">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 5, right: 5, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f1f23" vertical={false} />
          <XAxis
            dataKey="time"
            stroke="#52525b"
            fontSize={10}
            tickLine={false}
            axisLine={false}
            dy={10}
          />
          <Tooltip
            contentStyle={{ backgroundColor: '#09090b', border: '1px solid #27272a', borderRadius: '4px', fontSize: '10px' }}
          />
          <Line
            type="monotone"
            dataKey="v"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}