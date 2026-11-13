# Ship NMEA (UDP broadcast) -> virtual COM port. The program reads the other end of the pair.
$udpPort = 30000; $com = 'COM10'
while ($true) {
    try {
        $udp = New-Object System.Net.Sockets.UdpClient
        $udp.Client.SetSocketOption('Socket', 'ReuseAddress', $true)
        $udp.Client.Bind((New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any, $udpPort)))
        $sp = New-Object System.IO.Ports.SerialPort $com, 4800, 'None', 8, 'One'
        $sp.WriteTimeout = 500
        $sp.Open()
        $ep = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any, 0)
        while ($true) {
            $data = $udp.Receive([ref]$ep)
            try { $sp.Write($data, 0, $data.Length) } catch [System.TimeoutException] { }
        }
    } catch {
        Start-Sleep -Seconds 5      # COM or network gone: start over
    } finally {
        if ($sp) { $sp.Close() }; if ($udp) { $udp.Close() }
    }
}
