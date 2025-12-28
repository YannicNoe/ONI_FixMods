param(
    #[Parameter()]
    #[switch]$purgeAll = $false,

    [Parameter()] 
    [string]$modDirectory = "$([Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments))\Klei\OxygenNotIncluded\mods",

    [Parameter()] 
    [string]$backupDirectory = "$modDirectory\SteamModsBkp"
)

function Create-Browser {
    param(
        [Parameter(mandatory=$true)][ValidateSet('Chrome','Edge','Firefox')][string]$browser,      
        [Parameter(mandatory=$false)][bool]$HideCommandPrompt = $true,
        [Parameter(mandatory=$false)][string]$driverversion = '',      
        [Parameter(mandatory=$false)][object]$options = $null
    )
    $driver = $null

    function Load-NugetAssembly {
	    [CmdletBinding()]
	    param(
		    [string]$url,
		    [string]$name,
		    [string]$zipinternalpath,
		    [switch]$downloadonly
	    )
	    if($psscriptroot -ne ''){      
		    $localpath = join-path $psscriptroot $name
	    }else{
		    $localpath = join-path $env:TEMP $name
	    }
	    $tmp = "$env:TEMP\$([IO.Path]::GetRandomFileName())"      
	    $zip = $null
	    try{
		    if(!(Test-Path $localpath)){
			    Add-Type -A System.IO.Compression.FileSystem
			    write-host "Downloading and extracting required library '$name' ... " -F Green -NoNewline      
			    (New-Object System.Net.WebClient).DownloadFile($url, $tmp)
			    $zip = [System.IO.Compression.ZipFile]::OpenRead($tmp)
			    $zip.Entries | ?{$_.Fullname -eq $zipinternalpath} | %{
				    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($_,$localpath)
			    }
                write-host "OK" -F Green  
		    }
		    if (Get-Item $localpath -Stream zone.identifier -ea SilentlyContinue){
			    Unblock-File -Path $localpath
		    }
		    if(!$downloadonly.IsPresent){
			    Add-Type -Path $localpath -EA Stop
		    }
              
	    }catch{
		    throw "Error: $($_.Exception.Message)"      
	    }finally{
		    if ($zip){$zip.Dispose()}
		    if(Test-Path $tmp){del $tmp -Force -EA 0}
	    }
    }

    # Load Selenium Webdriver .NET Assembly and dependencies
    Load-NugetAssembly 'https://www.nuget.org/api/v2/package/Newtonsoft.Json' -name 'Newtonsoft.Json.dll' -zipinternalpath 'lib/net45/Newtonsoft.Json.dll' -EA Stop    
    Load-NugetAssembly 'https://www.nuget.org/api/v2/package/Selenium.WebDriver/4.23.0' -name 'WebDriver.dll' -zipinternalpath 'lib/netstandard2.0/WebDriver.dll' -EA Stop
    Load-NugetAssembly 'https://www.nuget.org/api/v2/package/Selenium.Support/4.23.0' -name 'WebDriver.Support.dll' -zipinternalpath 'lib/netstandard2.0/WebDriver.Support.dll' -EA Stop    
    Load-NugetAssembly 'https://www.nuget.org/api/v2/package/SeleniumExtras.WaitHelpers/1.0.2' -name 'SeleniumExtras.WaitHelpers.dll' -zipinternalpath 'lib/netstandard2.1/SeleniumExtras.WaitHelpers.dll' -EA Stop    
    
    if($psscriptroot -ne ''){      
        $driverpath = $psscriptroot
    }else{
        $driverpath = $env:TEMP
    }
    switch($browser){
        'Chrome' {      
            $chrome = Get-Package -Name 'Google Chrome' -EA SilentlyContinue | select -F 1      
            if (!$chrome){
                throw "Google Chrome Browser not installed."      
                return
            }
            Load-NugetAssembly "https://www.nuget.org/api/v2/package/Selenium.WebDriver.ChromeDriver/$driverversion" -name 'chromedriver.exe' -zipinternalpath 'driver/win32/chromedriver.exe' -downloadonly -EA Stop      
            # create driver service
            $dService = [OpenQA.Selenium.Chrome.ChromeDriverService]::CreateDefaultService($driverpath)
            # hide command prompt window
            $dService.HideCommandPromptWindow = $HideCommandPrompt
            # create driver object
            if ($options){
                $driver = New-Object OpenQA.Selenium.Chrome.ChromeDriver $dService,$options
            }else{
                $driver = New-Object OpenQA.Selenium.Chrome.ChromeDriver $dService
            }
        }
        'Edge' {      
            $edge = Get-Package -Name 'Microsoft Edge' -EA SilentlyContinue | select -F 1      
            if (!$edge){
                throw "Microsoft Edge Browser not installed."      
                return
            }
            Load-NugetAssembly "https://www.nuget.org/api/v2/package/Selenium.WebDriver.MSEdgeDriver.win32/$driverversion" -name 'msedgedriver.exe' -zipinternalpath 'driver/win32/msedgedriver.exe' -downloadonly -EA Stop      
            # create driver service
            $dService = [OpenQA.Selenium.Edge.EdgeDriverService]::CreateDefaultService($driverpath)
            # hide command prompt window
            $dService.HideCommandPromptWindow = $HideCommandPrompt
            # create driver object
            if ($options){
                $driver = New-Object OpenQA.Selenium.Edge.EdgeDriver $dService,$options
            }else{
                $driver = New-Object OpenQA.Selenium.Edge.EdgeDriver $dService
            }
        }
        'Firefox' {      
            $ff = Get-Package -Name "Mozilla Firefox*" -EA SilentlyContinue | select -F 1      
            if (!$ff){
                throw "Mozilla Firefox Browser not installed."      
                return
            }
            Load-NugetAssembly "https://www.nuget.org/api/v2/package/Selenium.WebDriver.GeckoDriver/$driverversion" -name 'geckodriver.exe' -zipinternalpath 'driver/win64/geckodriver.exe' -downloadonly -EA Stop      
            # create driver service
            $dService = [OpenQA.Selenium.Firefox.FirefoxDriverService]::CreateDefaultService($driverpath)
            # hide command prompt window
            $dService.HideCommandPromptWindow = $HideCommandPrompt
            # create driver object
            if($options){
                $driver = New-Object OpenQA.Selenium.Firefox.FirefoxDriver $dService, $options
            }else{
                $driver = New-Object OpenQA.Selenium.Firefox.FirefoxDriver $dService
            }
        }
    }
    return $driver
}

Function Invoke-SteamWorkshopSubscription {
    param (
        [Parameter(Mandatory)]
        [long]$WorkshopId,

        <#[Parameter(Mandatory)]
        [ValidateSet('Subscribe','Unsubscribe')]
        [string]$Action,#>

        [Parameter(Mandatory)]
        [OpenQA.Selenium.WebDriver]$Driver
    )

    $url = "https://steamcommunity.com/sharedfiles/filedetails/?id=$WorkshopId"
    $driver.Navigate().GoToUrl($url)
    $driver.executeScript("SubscribeItem( '$WorkshopId', '457140' );")
    Start-Sleep -Seconds 1
}

# Source - https://stackoverflow.com/a
# Posted by woxxom, modified by community. See post 'Timeline' for change history
# Retrieved 2025-12-28, License - CC BY-SA 4.0

function Parse-JsonFile([string]$file) {
    $text = [IO.File]::ReadAllText($file)
    $parser = New-Object Web.Script.Serialization.JavaScriptSerializer
    $parser.MaxJsonLength = $text.length
    Write-Output -NoEnumerate $parser.Deserialize($text, [hashtable])
    # To deserialize to a dictionary, use $parser.DeserializeObject($text) instead
}

$steamMods = "$modDirectory/Steam"

if (-not (Test-Path $steamMods)) {
    New-Item -Path $steamMods -ItemType Directory
}
Robocopy.exe $steamMods $backupDirectory /s /sec /a-:RH /mt:4 /xx /xo `
/XD anim assets archived_versions templates worldgen elements codex translations Source true_tiles_addon strings `
/XF mod_info.yaml mod.yaml *.pdb *.dll *.png *.jpg LauncherMetadata *.pot LICENSE *.md

<#
# Delete directories with specific names
Get-ChildItem -Path $backupDirectory -Recurse -Directory |
Where-Object { $_.Name -in @("anim","assets","archived_versions","templates", "worldgen", "elements", "codex","translations") } | 
#Select-Object FullName
Remove-Item -Recurse -Force

# Delete files with specific names or extensions
Get-ChildItem -Path $backupDirectory -Recurse -File |
Where-Object {
    $_.Name -in @("mod_info.yaml","mod.yaml") -or
    $_.Extension -in @(".pdb",".dll",".png")
} | 
#Select-Object FullName
Remove-Item -Force
#>

Stop-Process -Name "OneDrive" -Force
$driver = Create-Browser -browser Edge
$driver.Navigate().GoToUrl("https://steamcommunity.com/login/home")

[OpenQA.Selenium.Support.UI.WebDriverWait]$wait = new-object OpenQA.Selenium.Support.UI.WebDriverWait ($driver,[System.TimeSpan]::FromSeconds(90))
#$wait.Until([OpenQA.Selenium.By]::PartialLinkText("https://steamcommunity.com/profiles"))
$wait.Until([SeleniumExtras.WaitHelpers.ExpectedConditions]::UrlContains("https://steamcommunity.com/profiles"))

#$toBeDeleted = "anim", "assets", "archived_versions", "templates", "translations", "mod_info.yaml", "mod.yaml"
# *.pdb, *.dll, *.png

# Process mods.json
$modFile = "$modDirectory/mods.json"
$modBackupFile = "$modDirectory/FixMods.txt"

$mods = Parse-JsonFile $modFile
$processedMods = @()

#$modsPSO.psobject.properties | Foreach { $mods[$_.Name] = $_.Value }

Set-Content -Path $modBackupFile -Value ""

foreach ($mod in $mods.mods) {
    #if (($mod.status -eq 1 -or -not $purgeAll) -or $mod.staticID -cmatch '[.]Local$') {
    if ($mod.status -eq 1 -or $mod.staticID -cmatch '[.]Local$') {
        continue
    } else {
        $modId = $mod.label.id
        if ( $modId -eq "" -or $null -eq $modId -or $modId -eq 0) { continue }
        Add-Content -Path $modBackupFile -Value $modId
        Invoke-SteamWorkshopSubscription -Driver $driver -WorkshopId $modId
        Remove-Item -Path "$steamMods/$modId" -Force -Recurse -Confirm:$false
        $processedMods += $modId
    }
}

Start-Process steam://rungameid/457140
Read-host "Press ENTER after start of Oxygen not Included..."

Get-Process "OxygenNotIncluded" | Stop-Process -Force

foreach ($mod in $processedMods) {
    Invoke-SteamWorkshopSubscription -Driver $driver -WorkshopId $mod
}

Start-Process steam://rungameid/457140
Read-host "Press ENTER after start of Oxygen not Included..."
Get-Process "OxygenNotIncluded" | Stop-Process -Force

foreach ($mod in $processedMods) {
    if (Test-Path "$backupDirectory/$mod") {
        Copy-Item -Path "$backupDirectory/$mod" -Destination "$steamMods" -Force
    }
}

$driver.Close()
